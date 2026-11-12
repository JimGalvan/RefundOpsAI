import base64
import json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from email import policy
from email.parser import BytesParser
from typing import Any

import httpx
import pytest

from paypal_client import PayPalClient, PayPalError
from paypal_client.client import BASE_URL


def body(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content)


def token_response(token: str = "test-token", expires_in: int = 3600) -> httpx.Response:
    return httpx.Response(200, json={"access_token": token, "expires_in": expires_in})


def test_token_request_and_cache(
    recorded_client: tuple[PayPalClient, list[httpx.Request]],
) -> None:
    client, requests = recorded_client
    assert client.access_token() == client.access_token() == "test-token"
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == BASE_URL + "/v1/oauth2/token"
    assert request.method == "POST"
    assert (
        request.headers["authorization"]
        == "Basic " + base64.b64encode(b"test-client:test-secret").decode()
    )
    assert request.headers["content-type"].startswith(
        "application/x-www-form-urlencoded"
    )
    assert request.content == b"grant_type=client_credentials"
    assert len(request.headers["PayPal-Request-Id"]) == 32


def test_token_expiry_margin(
    client_factory: Callable[..., PayPalClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [100.0]
    monkeypatch.setattr("paypal_client.client.time.monotonic", lambda: now[0])
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return token_response(f"token-{len(requests)}", expires_in=120)

    client = client_factory(handle)
    assert client.access_token() == "token-1"
    now[0] = 159.0
    assert client.access_token() == "token-1"
    now[0] = 160.0
    assert client.access_token() == "token-2"


def test_token_cache_is_thread_safe(
    client_factory: Callable[..., PayPalClient],
) -> None:
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return token_response()

    client = client_factory(handle)
    with ThreadPoolExecutor(max_workers=8) as pool:
        tokens = list(pool.map(lambda _: client.access_token(), range(24)))
    assert tokens == ["test-token"] * 24
    assert len(calls) == 1


@pytest.mark.parametrize("final_status", [200, 401])
def test_refresh_once_on_401(
    client_factory: Callable[..., PayPalClient],
    final_status: int,
) -> None:
    token_calls = 0
    api_calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path == "/v1/oauth2/token":
            token_calls += 1
            return token_response(f"token-{token_calls}")
        api_calls.append(request)
        if len(api_calls) == 1:
            return httpx.Response(401, json={"name": "AUTHENTICATION_FAILURE"})
        return httpx.Response(final_status, json={"id": "order"})

    client = client_factory(handle)
    if final_status == 401:
        with pytest.raises(PayPalError):
            client.capture_order("order", request_id="persistent-id")
    else:
        assert client.capture_order("order", request_id="persistent-id") == {
            "id": "order"
        }
    assert token_calls == len(api_calls) == 2
    assert [request.headers["authorization"] for request in api_calls] == [
        "Bearer token-1",
        "Bearer token-2",
    ]
    assert {request.headers["PayPal-Request-Id"] for request in api_calls} == {
        "persistent-id"
    }


def test_create_order_shape(
    recorded_client: tuple[PayPalClient, list[httpx.Request]],
) -> None:
    client, requests = recorded_client
    client.create_order(
        amount=Decimal("120"),
        invoice_id="INV-1",
        custom_id="CASE-A",
        description="Headphones",
        shipping={
            "name": "Sarah",
            "line1": "418 Alder",
            "line2": "Apt 3B",
            "city": "Portland",
            "state": "OR",
            "postal_code": "97205",
            "country_code": "US",
        },
        request_id="create-id",
        return_url="https://merchant.example/return",
        cancel_url="https://merchant.example/cancel",
    )
    request = requests[-1]
    assert request.method == "POST"
    assert request.url.path == "/v2/checkout/orders"
    assert request.headers["PayPal-Request-Id"] == "create-id"
    payload = body(request)
    assert payload["intent"] == "CAPTURE"
    assert payload["purchase_units"] == [
        {
            "reference_id": "INV-1",
            "invoice_id": "INV-1",
            "custom_id": "CASE-A",
            "description": "Headphones",
            "amount": {"currency_code": "USD", "value": "120.00"},
            "shipping": {
                "type": "SHIPPING",
                "name": {"full_name": "Sarah"},
                "address": {
                    "address_line_1": "418 Alder",
                    "address_line_2": "Apt 3B",
                    "admin_area_2": "Portland",
                    "admin_area_1": "OR",
                    "postal_code": "97205",
                    "country_code": "US",
                },
            },
        }
    ]
    assert payload["payment_source"]["paypal"]["experience_context"] == {
        "payment_method_preference": "IMMEDIATE_PAYMENT_REQUIRED",
        "shipping_preference": "SET_PROVIDED_ADDRESS",
        "user_action": "PAY_NOW",
        "return_url": "https://merchant.example/return",
        "cancel_url": "https://merchant.example/cancel",
    }
    assert "application_context" not in payload


def test_create_omits_none(
    recorded_client: tuple[PayPalClient, list[httpx.Request]],
) -> None:
    client, requests = recorded_client
    client.create_order(
        amount="90",
        invoice_id="INV-2",
        shipping={"name": "Marcus", "line1": "92 Cedar"},
    )
    unit = body(requests[-1])["purchase_units"][0]
    assert unit["custom_id"] == "INV-2"
    assert "description" not in unit
    assert unit["shipping"]["address"] == {"address_line_1": "92 Cedar"}
    assert "null" not in requests[-1].content.decode()
    client.create_order(amount="90.0", invoice_id="INV-3")
    assert "shipping" not in body(requests[-1])["purchase_units"][0]
    assert (
        requests[-1].headers["PayPal-Request-Id"]
        != requests[-2].headers["PayPal-Request-Id"]
    )


@pytest.mark.parametrize("value", ["NaN", "Infinity", "not-money"])
def test_invalid_amount_never_sends(
    recorded_client: tuple[PayPalClient, list[httpx.Request]], value: str
) -> None:
    client, requests = recorded_client
    with pytest.raises(ValueError):
        client.create_order(amount=value, invoice_id="INV")
    assert requests == []


@pytest.mark.parametrize("rel", ["payer-action", "approve"])
def test_approval_url(rel: str) -> None:
    assert (
        PayPalClient.approval_url(
            {
                "links": [
                    {"rel": "self", "href": "ignore"},
                    {"rel": rel, "href": "approve-here"},
                ]
            }
        )
        == "approve-here"
    )


def test_missing_approval_url() -> None:
    with pytest.raises(ValueError, match="no payer-action or approve"):
        PayPalClient.approval_url({})


@pytest.mark.parametrize(
    ("method", "identifier", "path"),
    [
        ("get_order", "ORDER", "/v2/checkout/orders/ORDER"),
        ("get_capture", "CAPTURE", "/v2/payments/captures/CAPTURE"),
        ("get_refund", "REFUND", "/v2/payments/refunds/REFUND"),
        ("get_dispute", "DISPUTE", "/v1/customer/disputes/DISPUTE"),
    ],
)
def test_get_calls(
    recorded_client: tuple[PayPalClient, list[httpx.Request]],
    method: str,
    identifier: str,
    path: str,
) -> None:
    client, requests = recorded_client
    assert getattr(client, method)(identifier) == {"id": "result-id"}
    assert requests[-1].method == "GET"
    assert requests[-1].url.path == path
    assert requests[-1].headers["authorization"] == "Bearer test-token"
    assert "PayPal-Request-Id" not in requests[-1].headers


def test_capture_and_refund(
    recorded_client: tuple[PayPalClient, list[httpx.Request]],
) -> None:
    client, requests = recorded_client
    assert client.capture_order("ORDER") == {"id": "result-id"}
    assert requests[-1].url.path == "/v2/checkout/orders/ORDER/capture"
    client.refund_capture("CAPTURE")
    assert requests[-1].url.path == "/v2/payments/captures/CAPTURE/refund"
    assert body(requests[-1]) == {}
    client.refund_capture(
        "CAPTURE",
        amount=Decimal("25"),
        currency="EUR",
        note_to_payer="Thanks",
        invoice_id="REF-1",
    )
    assert body(requests[-1]) == {
        "amount": {"currency_code": "EUR", "value": "25.00"},
        "note_to_payer": "Thanks",
        "invoice_id": "REF-1",
    }
    assert all(request.headers.get("PayPal-Request-Id") for request in requests)


def test_extract_order_id() -> None:
    assert (
        PayPalClient.extract_order_id(
            {"supplementary_data": {"related_ids": {"order_id": "ORDER"}}}
        )
        == "ORDER"
    )
    for capture in ({}, {"supplementary_data": {}}, {"supplementary_data": None}):
        assert PayPalClient.extract_order_id(capture) is None


@pytest.mark.parametrize("payload", [{"items": [{"dispute_id": "D-1"}]}, {}])
def test_list_disputes(
    client_factory: Callable[..., PayPalClient], payload: dict[str, Any]
) -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return (
            token_response()
            if request.url.path == "/v1/oauth2/token"
            else httpx.Response(200, json=payload)
        )

    client = client_factory(handle)
    assert client.list_disputes(
        dispute_state="OPEN",
        start_time="2026-11-01T00:00:00Z",
        disputed_transaction_id="TX-1",
        page_size=20,
    ) == payload.get("items", [])
    assert requests[-1].url.path == "/v1/customer/disputes"
    assert dict(requests[-1].url.params) == {
        "dispute_state": "OPEN",
        "start_time": "2026-11-01T00:00:00Z",
        "disputed_transaction_id": "TX-1",
        "page_size": "20",
    }
    client.list_disputes()
    assert dict(requests[-1].url.params) == {"page_size": "50"}


def test_claim_and_offer(
    recorded_client: tuple[PayPalClient, list[httpx.Request]],
) -> None:
    client, requests = recorded_client
    client.accept_claim("D-1", note="Returned")
    assert requests[-1].url.path.endswith("/D-1/accept-claim")
    assert body(requests[-1]) == {
        "note": "Returned",
        "accept_claim_reason": "REASON_NOT_SET",
        "accept_claim_type": "REFUND",
    }
    client.accept_claim(
        "D-1", note="Returned", refund_amount="120", currency="USD", invoice_id="INV-1"
    )
    assert body(requests[-1])["refund_amount"] == {
        "currency_code": "USD",
        "value": "120.00",
    }
    assert body(requests[-1])["invoice_id"] == "INV-1"
    client.make_offer("D-2", note="Goodwill", amount="25", invoice_id="INV-2")
    assert requests[-1].url.path.endswith("/D-2/make-offer")
    assert body(requests[-1]) == {
        "note": "Goodwill",
        "offer_amount": {"currency_code": "USD", "value": "25.00"},
        "offer_type": "REFUND",
        "invoice_id": "INV-2",
    }


@pytest.mark.parametrize("note", [None, "Carrier confirms delivery"])
def test_evidence_multipart(
    recorded_client: tuple[PayPalClient, list[httpx.Request]], note: str | None
) -> None:
    client, requests = recorded_client
    evidences = [
        {
            "evidence_type": "PROOF_OF_DELIVERY",
            "evidence_info": {"tracking_number": "TRACK-1"},
        }
    ]
    client.provide_evidence("D-1", evidences=evidences, note=note)
    request = requests[-1]
    assert request.url.path.endswith("/D-1/provide-evidence")
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
        + request.content,
    )
    parts = list(message.iter_parts())
    assert len(parts) == 1
    assert parts[0].get_param("name", header="content-disposition") == "input"
    assert parts[0].get_filename() is None
    assert parts[0].get_content_type() == "application/json"
    expected = {"evidences": evidences}
    if note is not None:
        expected["note"] = note
    assert json.loads(parts[0].get_payload(decode=True)) == expected


def test_messages_and_sandbox_helpers(
    recorded_client: tuple[PayPalClient, list[httpx.Request]],
) -> None:
    client, requests = recorded_client
    client.send_message("D-1", "Checking delivery", request_id="message-id")
    assert requests[-1].url.path.endswith("/D-1/send-message")
    assert body(requests[-1]) == {"message": "Checking delivery"}
    assert requests[-1].headers["PayPal-Request-Id"] == "message-id"
    client.sandbox_require_evidence("D-1")
    assert requests[-1].url.path.endswith("/D-1/require-evidence")
    assert body(requests[-1]) == {"action": "SELLER_EVIDENCE"}
    client.sandbox_adjudicate("D-1")
    assert requests[-1].url.path.endswith("/D-1/adjudicate")
    assert body(requests[-1]) == {"adjudication_outcome": "BUYER_FAVOR"}
    assert all(request.headers.get("PayPal-Request-Id") for request in requests)


@pytest.mark.parametrize("oauth", [False, True])
def test_error_mapping(
    client_factory: Callable[..., PayPalClient], oauth: bool
) -> None:
    raw = (
        {"error": "invalid_client", "error_description": "Invalid credentials"}
        if oauth
        else {
            "name": "UNPROCESSABLE_ENTITY",
            "message": "Cannot refund",
            "debug_id": "debug-123",
            "details": [{"issue": "LIMIT", "description": "Refund exceeds capture"}],
        }
    )

    def handle(request: httpx.Request) -> httpx.Response:
        if not oauth and request.url.path == "/v1/oauth2/token":
            return token_response()
        return httpx.Response(
            400 if oauth else 422, json=raw, headers={"PayPal-Debug-Id": "debug-123"}
        )

    client = client_factory(handle)
    with pytest.raises(PayPalError) as raised:
        client.get_order("ORDER")
    error = raised.value
    assert error.status == (400 if oauth else 422)
    assert error.name == ("invalid_client" if oauth else "UNPROCESSABLE_ENTITY")
    assert error.message == ("Invalid credentials" if oauth else "Cannot refund")
    assert error.raw == raw
    assert error.details == raw.get("details", [])
    assert error.debug_id == "debug-123"
    for value in (str(error.status), error.name, error.message, "debug-123"):
        assert value in str(error)
    assert all(
        secret not in str(error)
        for secret in ("test-secret", "test-token", "Authorization")
    )


def test_error_and_logging_redaction(
    client_factory: Callable[..., PayPalClient], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="paypal_client")

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return token_response()
        return httpx.Response(
            400,
            json={
                "name": "BAD",
                "debug_id": "trace",
                "message": "test-secret test-token\nAuthorization: Bearer another-token",
            },
        )

    with pytest.raises(PayPalError) as raised:
        client_factory(handle).get_order("ORDER")
    output = str(raised.value) + caplog.text
    for secret in (
        "test-secret",
        "test-client",
        "test-token",
        "another-token",
        "Authorization",
        "grant_type",
    ):
        assert secret not in output
    assert "\n" not in str(raised.value)
    assert "debug_id=trace" in str(raised.value)
    assert "GET /v2/checkout/orders/ORDER status=400 debug_id=trace" in caplog.text


def test_non_json_error_and_empty_success(
    client_factory: Callable[..., PayPalClient],
) -> None:
    responses = [httpx.Response(400, text="upstream error"), httpx.Response(204)]

    def handle(request: httpx.Request) -> httpx.Response:
        return (
            token_response()
            if request.url.path == "/v1/oauth2/token"
            else responses.pop(0)
        )

    client = client_factory(handle)
    with pytest.raises(PayPalError) as raised:
        client.get_order("ORDER")
    assert raised.value.name == "HTTP_ERROR"
    assert raised.value.raw == "upstream error"
    assert client.send_message("D-1", "hello") == {}


@pytest.mark.parametrize("status", [429, 500, 503, 599])
@pytest.mark.parametrize("post", [True, False])
def test_retries_only_with_request_id(
    client_factory: Callable[..., PayPalClient],
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    post: bool,
) -> None:
    requests: list[httpx.Request] = []
    sleeps: list[float] = []
    monkeypatch.setattr("paypal_client.client.time.sleep", sleeps.append)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return token_response()
        requests.append(request)
        if len(requests) < 3:
            return httpx.Response(status, json={"name": "UNAVAILABLE"})
        return httpx.Response(200, json={"id": "captured"})

    client = client_factory(handle)
    if post:
        assert client.capture_order("ORDER", request_id="same-id") == {"id": "captured"}
        assert len(requests) == 3
        assert {request.headers["PayPal-Request-Id"] for request in requests} == {
            "same-id"
        }
        assert sleeps == [0.1, 0.2]
    else:
        with pytest.raises(PayPalError):
            client.get_order("ORDER")
        assert len(requests) == 1
        assert sleeps == []


@pytest.mark.parametrize("status", [400, 422, 503])
def test_bounded_retry(
    client_factory: Callable[..., PayPalClient],
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    requests: list[httpx.Request] = []
    monkeypatch.setattr("paypal_client.client.time.sleep", lambda _: None)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return token_response()
        requests.append(request)
        return httpx.Response(status, json={"name": "ERROR"})

    with pytest.raises(PayPalError):
        client_factory(handle).capture_order("ORDER")
    assert len(requests) == (3 if status == 503 else 1)


def test_network_errors_not_retried(
    client_factory: Callable[..., PayPalClient],
) -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path == "/v1/oauth2/token":
            return token_response()
        calls += 1
        raise httpx.ReadTimeout("timeout", request=request)

    with pytest.raises(httpx.ReadTimeout):
        client_factory(handle).capture_order("ORDER")
    assert calls == 1


def test_retry_budget_survives_auth_refresh(
    client_factory: Callable[..., PayPalClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("paypal_client.client.time.sleep", lambda _: None)
    statuses = iter([503, 401, 503, 503])
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return token_response()
        calls.append(request)
        return httpx.Response(next(statuses), json={"name": "TEMPORARY_ERROR"})

    with pytest.raises(PayPalError) as raised:
        client_factory(handle).capture_order("ORDER")
    assert raised.value.status == 503
    assert len(calls) == 4  # Initial call, one auth refresh, and two transient retries.
    assert len({request.headers["PayPal-Request-Id"] for request in calls}) == 1


def test_redirects_cannot_escape_sandbox() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/oauth2/token":
            return token_response()
        return httpx.Response(
            302, headers={"Location": "https://api-m.paypal.com/live"}
        )

    with httpx.Client(
        transport=httpx.MockTransport(handle), follow_redirects=True
    ) as transport:
        client = PayPalClient("test-client", "test-secret", client=transport)
        with pytest.raises(PayPalError) as raised:
            client.get_order("ORDER")
    assert raised.value.status == 302
    assert len(requests) == 2
    assert all(request.url.host == "api-m.sandbox.paypal.com" for request in requests)


@pytest.mark.parametrize("env", ["live", "production", "SANDBOX", ""])
def test_sandbox_guard(env: str) -> None:
    with pytest.raises(ValueError, match="sandbox"):
        PayPalClient("id", "secret", env=env)


def test_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "env-id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "env-secret")
    monkeypatch.delenv("PAYPAL_ENV", raising=False)
    with PayPalClient.from_env() as client:
        assert client._client_id == "env-id"
        assert client._client_secret == "env-secret"
    monkeypatch.setenv("PAYPAL_ENV", "live")
    with pytest.raises(ValueError, match="sandbox"):
        PayPalClient.from_env()


@pytest.mark.parametrize("verification_status", ["SUCCESS", "FAILURE", None])
def test_webhook_verification(
    client_factory: Callable[..., PayPalClient], verification_status: str | None
) -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return (
            token_response()
            if request.url.path == "/v1/oauth2/token"
            else httpx.Response(200, json={"verification_status": verification_status})
        )

    client = client_factory(handle)
    assert client.verify_webhook_signature(
        headers={
            "paypal-auth-algo": "SHA256withRSA",
            "PayPal-Cert-Url": "https://example.com/cert",
            "PAYPAL-TRANSMISSION-ID": "tx-id",
            "paypal-transmission-sig": "signed",
            "Paypal-Transmission-Time": "2026-11-11T12:00:00Z",
        },
        body=b'{"id":"event-id"}',
        webhook_id="webhook-id",
    ) is (verification_status == "SUCCESS")
    assert requests[-1].url.path == "/v1/notifications/verify-webhook-signature"
    assert body(requests[-1]) == {
        "auth_algo": "SHA256withRSA",
        "cert_url": "https://example.com/cert",
        "transmission_id": "tx-id",
        "transmission_sig": "signed",
        "transmission_time": "2026-11-11T12:00:00Z",
        "webhook_id": "webhook-id",
        "webhook_event": {"id": "event-id"},
    }
    assert all(request.headers.get("PayPal-Request-Id") for request in requests)
