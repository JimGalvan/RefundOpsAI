import json
import logging
from collections.abc import Iterator, Mapping
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from django.core.cache import cache
from django.http import HttpResponse
from django.test import Client
from django.urls import path

from cases.models import Case
from console.webhooks import paypal_webhook
from paypal_client import PayPalClient, PayPalError

urlpatterns = [path("paypal/webhook/", paypal_webhook)]
pytestmark = pytest.mark.django_db
URL = "/paypal/webhook/"


class FakeClient:
    extract_order_id = staticmethod(PayPalClient.extract_order_id)

    def __init__(self) -> None:
        fixture = Path(__file__).parents[1] / "cases/fixtures/intake_120.json"
        self.records = json.loads(fixture.read_text(encoding="utf-8"))
        self.verified = True
        self.verify_error: Exception | None = None
        self.sync_error: Exception | None = None
        self.verifications: list[tuple[Mapping[str, str], bytes, str]] = []
        self.calls: list[tuple[str, str]] = []
        self.closed = False

    def verify_webhook_signature(
        self, *, headers: Mapping[str, str], body: bytes, webhook_id: str
    ) -> bool:
        self.verifications.append((headers, body, webhook_id))
        if self.verify_error:
            raise self.verify_error
        return self.verified

    def get_dispute(self, dispute_id: str) -> dict[str, Any]:
        self.calls.append(("dispute", dispute_id))
        if self.sync_error:
            raise self.sync_error
        assert dispute_id == self.records["dispute"]["dispute_id"]
        return deepcopy(self.records["dispute"])

    def get_capture(self, capture_id: str) -> dict[str, Any]:
        self.calls.append(("capture", capture_id))
        assert capture_id == self.records["capture"]["id"]
        return deepcopy(self.records["capture"])

    def get_order(self, order_id: str) -> dict[str, Any]:
        self.calls.append(("order", order_id))
        assert order_id == self.records["order"]["id"]
        return deepcopy(self.records["order"])

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def isolated_settings(settings: Any) -> Iterator[None]:
    settings.ROOT_URLCONF = __name__
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "paypal-webhook-tests",
        }
    }
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    client = FakeClient()
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WEBHOOK-TEST")
    monkeypatch.setattr("console.webhooks.PayPalClient.from_env", lambda: client)
    return client


def event(
    event_type: str = "CUSTOMER.DISPUTE.CREATED", event_id: str = "WH-TEST-1"
) -> dict[str, Any]:
    return {
        "id": event_id,
        "event_type": event_type,
        "resource": {"dispute_id": "PP-D-27803"},
        "private_note": "BODY-SECRET-MUST-NOT-BE-LOGGED",
    }


def post(client: Client, payload: Any = None) -> HttpResponse:
    return client.post(
        URL,
        data=json.dumps(event() if payload is None else payload),
        content_type="application/json",
        HTTP_PAYPAL_TRANSMISSION_ID="TEST-TRANSMISSION",
        HTTP_AUTHORIZATION="Bearer HEADER-SECRET-MUST-NOT-BE-LOGGED",
    )


def test_created_creates_case(client: Client, fake: FakeClient) -> None:
    response = post(client)
    case = Case.objects.get()
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "case": case.pk}
    assert case.paypal_dispute_id == "PP-D-27803"
    assert case.paypal_snapshot == fake.records
    assert case.amount == Decimal("120.00")
    assert fake.calls == [
        ("dispute", "PP-D-27803"),
        ("capture", "CAP-120"),
        ("order", "PAYPAL-ORDER-120"),
    ]
    headers, body, webhook_id = fake.verifications[0]
    assert body == json.dumps(event()).encode()
    assert headers["PayPal-Transmission-Id"] == "TEST-TRANSMISSION"
    assert webhook_id == "WEBHOOK-TEST"
    assert fake.closed
    assert cache.get("paypal-webhook:WH-TEST-1") is True


def test_repeat_syncs_once(client: Client, fake: FakeClient) -> None:
    assert post(client).status_code == 200
    response = post(client)
    assert response.status_code == 200
    assert response.json() == {"status": "duplicate"}
    assert Case.objects.count() == 1
    assert len(fake.calls) == 3
    assert len(fake.verifications) == 2


@pytest.mark.parametrize(
    "event_type", ["CUSTOMER.DISPUTE.UPDATED", "CUSTOMER.DISPUTE.RESOLVED"]
)
def test_refreshes_same_case(client: Client, fake: FakeClient, event_type: str) -> None:
    post(client)
    case = Case.objects.get()
    case.state = Case.State.READY
    case.save()
    fake.records["dispute"]["status"] = "RESOLVED"
    response = post(client, event(event_type, "WH-TEST-2"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "case": case.pk}
    case.refresh_from_db()
    assert case.dispute_status == "RESOLVED"
    assert case.state == Case.State.READY
    assert Case.objects.count() == 1
    assert len(fake.calls) == 6


@pytest.mark.parametrize(
    "error",
    [
        None,
        PayPalError(401, "DENIED", "secret detail"),
        httpx.ConnectError("secret detail"),
        KeyError("paypal-transmission-sig"),
        ValueError("malformed JSON"),
    ],
)
def test_invalid_signature(
    client: Client, fake: FakeClient, error: Exception | None
) -> None:
    fake.verified = False
    fake.verify_error = error
    response = post(client)
    assert response.status_code == 400
    assert response.json() == {"error": "invalid signature"}
    assert not fake.calls
    assert not Case.objects.exists()
    assert fake.closed
    assert cache.get("paypal-webhook:WH-TEST-1") is None


def test_duplicate_still_requires_verification(
    client: Client, fake: FakeClient
) -> None:
    post(client)
    fake.verified = False
    assert post(client).status_code == 400
    assert len(fake.calls) == 3


@pytest.mark.parametrize("webhook_id", [None, "", "   "])
def test_missing_webhook_id(
    client: Client,
    fake: FakeClient,
    monkeypatch: pytest.MonkeyPatch,
    webhook_id: str | None,
) -> None:
    if webhook_id is None:
        monkeypatch.delenv("PAYPAL_WEBHOOK_ID")
    else:
        monkeypatch.setenv("PAYPAL_WEBHOOK_ID", webhook_id)

    def unexpected_client() -> None:
        pytest.fail("Unconfigured webhook must not construct a PayPal client")

    monkeypatch.setattr("console.webhooks.PayPalClient.from_env", unexpected_client)
    response = post(client)
    assert response.status_code == 503
    assert response.json() == {"error": "webhook not configured"}
    assert not fake.calls


def test_unrelated_event(client: Client, fake: FakeClient) -> None:
    payload = event("PAYMENT.CAPTURE.COMPLETED")
    payload["resource"] = {"id": "CAP-120"}
    response = post(client, payload)
    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
    assert not fake.calls
    assert not Case.objects.exists()
    assert post(client, payload).json() == {"status": "duplicate"}


@pytest.mark.parametrize("method", ["get", "put", "patch", "delete", "head"])
def test_post_only(client: Client, fake: FakeClient, method: str) -> None:
    response = getattr(client, method)(URL)
    assert response.status_code == 405
    assert response.headers["Allow"] == "POST"
    assert not fake.verifications


def test_csrf_exempt(fake: FakeClient) -> None:
    assert post(Client(enforce_csrf_checks=True)).status_code == 200


@pytest.mark.parametrize("limit", [None, 512])
def test_oversized_body(
    client: Client, fake: FakeClient, settings: Any, limit: int | None
) -> None:
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = limit
    response = client.post(
        URL, data=b"x" * (1024 * 1024 + 1), content_type="application/json"
    )
    assert response.status_code == 413
    assert not fake.verifications
    assert not fake.calls


@pytest.mark.parametrize("body", [b"not JSON", b"[]", b"null", b'"text"', b"\xff"])
def test_bad_json(client: Client, fake: FakeClient, body: bytes) -> None:
    response = client.post(URL, data=body, content_type="application/json")
    assert response.status_code == 400
    assert len(fake.verifications) == 1
    assert not fake.calls


@pytest.mark.parametrize(
    "changes",
    [
        {"id": None},
        {"id": ""},
        {"id": []},
        {"event_type": []},
        {"resource": None},
        {"resource": {}},
        {"resource": {"dispute_id": []}},
        {"resource": {"dispute_id": ""}},
    ],
)
def test_malformed_event(
    client: Client, fake: FakeClient, changes: dict[str, Any]
) -> None:
    response = post(client, {**event(), **changes})
    assert response.status_code == 400
    assert not fake.calls
    assert cache.get("paypal-webhook:WH-TEST-1") is None


def test_sync_failure_can_retry(client: Client, fake: FakeClient) -> None:
    fake.sync_error = PayPalError(500, "FAILED", "SYNC-SECRET-MUST-NOT-BE-LOGGED")
    response = post(client)
    assert response.status_code == 502
    assert response.json() == {"error": "sync failed"}
    assert not Case.objects.exists()
    assert cache.get("paypal-webhook:WH-TEST-1") is None
    fake.sync_error = None
    assert post(client).status_code == 200
    assert Case.objects.count() == 1


@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout("SYNC-SECRET-MUST-NOT-BE-LOGGED"), ValueError("SYNC-SECRET-MUST-NOT-BE-LOGGED")]
)
def test_sync_network_or_parse_error_can_retry(client: Client, fake: FakeClient, error: Exception) -> None:
    fake.sync_error = error
    response = post(client)
    assert response.status_code == 502
    assert response.json() == {"error": "sync failed"}
    assert cache.get("paypal-webhook:WH-TEST-1") is None


def test_missing_rest_keys_answer_503(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WEBHOOK-TEST")

    def no_keys() -> None:
        raise ValueError("PayPal client id and secret are required")

    monkeypatch.setattr("console.webhooks.PayPalClient.from_env", no_keys)
    response = post(client)
    assert response.status_code == 503
    assert response.json() == {"error": "paypal not configured"}


def test_route_is_wired_into_the_site_urls(client: Client, settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    settings.ROOT_URLCONF = "RefundOpsAI.urls"
    monkeypatch.delenv("PAYPAL_WEBHOOK_ID", raising=False)
    response = client.post("/paypal/webhook/", data="{}", content_type="application/json")
    assert response.status_code == 503
    assert response.json() == {"error": "webhook not configured"}


def test_logs_only_event_identifiers(
    client: Client, fake: FakeClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        post(client)
        post(client)
        fake.sync_error = PayPalError(500, "FAILED", "SYNC-SECRET-MUST-NOT-BE-LOGGED")
        post(client, event(event_id="WH-TEST-2"))
        fake.verify_error = httpx.ConnectError("VERIFY-SECRET-MUST-NOT-BE-LOGGED")
        post(client)
    assert "CUSTOMER.DISPUTE.CREATED" in caplog.text
    assert "WH-TEST-1" in caplog.text
    assert "PP-D-27803" in caplog.text
    assert json.dumps(event()) not in caplog.text
    assert "MUST-NOT-BE-LOGGED" not in caplog.text
