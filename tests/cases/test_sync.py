import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from cases.models import Case
from cases.sync import allowed_actions, parse_dispute, sync_disputes, sync_one
from paypal_client import PayPalClient, PayPalError

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str = "intake_120") -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class FakeClient:
    extract_order_id = staticmethod(PayPalClient.extract_order_id)

    def __init__(self) -> None:
        self.records = [load_fixture(name) for name in ("intake_120", "intake_90", "intake_missing")]
        self.failures: dict[str, Exception] = {}
        self.calls: list[tuple[str, str | None]] = []
        self.closed = False

    def list_disputes(self, *, dispute_state: str | None = None) -> list[dict]:
        self.calls.append(("list", dispute_state))
        return [{"dispute_id": record["dispute"]["dispute_id"]} for record in self.records]

    def _get(self, kind: str, identifier: str) -> dict:
        self.calls.append((kind, identifier))
        if identifier in self.failures:
            raise self.failures[identifier]
        key = "dispute_id" if kind == "dispute" else "id"
        return deepcopy(next(record[kind] for record in self.records if record[kind] and record[kind].get(key) == identifier))

    def get_dispute(self, dispute_id: str) -> dict:
        return self._get("dispute", dispute_id)

    def get_capture(self, capture_id: str) -> dict:
        return self._get("capture", capture_id)

    def get_order(self, order_id: str) -> dict:
        return self._get("order", order_id)

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("name", ["intake_120", "intake_90"])
def test_allowed_actions(name: str) -> None:
    assert allowed_actions(load_fixture(name)["dispute"]) == ["ACCEPT_CLAIM", "MAKE_OFFER", "PROVIDE_EVIDENCE"]


def test_allowed_options_authoritative() -> None:
    assert allowed_actions({"allowed_response_options": {}, "links": [{"rel": "accept-claim"}]}) == []
    assert allowed_actions({"allowed_response_options": {"make_offer": None, "unknown": {}}}) == ["MAKE_OFFER"]
    assert allowed_actions({}) == []


def test_parse_all_fields() -> None:
    fixture = load_fixture()
    original = deepcopy(fixture)
    assert parse_dispute(**fixture) == {
        "paypal_dispute_id": "PP-D-27803", "paypal_capture_id": "CAP-120",
        "paypal_order_id": "PAYPAL-ORDER-120", "order_ref": "ORD-10482",
        "buyer_name": "Sarah Okafor", "buyer_email": "sarah@example.com",
        "amount": Decimal("120.00"), "currency": "USD",
        "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED", "stage": "INQUIRY",
        "dispute_status": "WAITING_FOR_SELLER_RESPONSE",
        "response_due_at": datetime(2026, 11, 26, 7, 59, 59, tzinfo=UTC),
        "allowed_actions": ["ACCEPT_CLAIM", "MAKE_OFFER", "PROVIDE_EVIDENCE"],
        "paypal_snapshot": fixture,
    }
    assert fixture == original


def test_parse_missing() -> None:
    assert parse_dispute({}, None, None) == {
        "paypal_dispute_id": "", "paypal_capture_id": "", "paypal_order_id": "",
        "order_ref": "", "buyer_name": "", "buyer_email": "",
        "amount": Decimal("0"), "currency": "", "reason": "", "stage": "",
        "dispute_status": "", "response_due_at": None, "allowed_actions": [],
        "paypal_snapshot": {"dispute": {}, "capture": None, "order": None},
    }
    assert parse_dispute({"disputed_transactions": [], "seller_response_due_date": "invalid"}, {}, {"purchase_units": []})["response_due_at"] is None


def test_parse_fallbacks() -> None:
    fixture = load_fixture()
    dispute, order = fixture["dispute"], fixture["order"]
    del dispute["dispute_amount"]
    transaction = dispute["disputed_transactions"][0]
    transaction["buyer"] = {}
    order["purchase_units"][0]["invoice_id"] = "ORDER-INVOICE"
    parsed = parse_dispute(dispute, None, order)
    assert parsed["order_ref"] == "ORDER-INVOICE"
    assert parsed["buyer_name"] == "Sarah Okafor"
    assert parsed["buyer_email"] == "sarah@example.com"
    assert parsed["amount"] == Decimal("120.00")
    assert parsed["currency"] == "USD"
    assert parsed["paypal_order_id"] == ""
    assert parse_dispute(dispute, None, None)["order_ref"] == "ORD-10482"
    del transaction["invoice_number"]
    transaction["custom"] = "CUSTOM-REF"
    assert parse_dispute(dispute, None, None)["order_ref"] == "CUSTOM-REF"
    dispute["seller_response_due_date"] = "2026-11-26T07:59:59"
    assert parse_dispute(dispute, None, None)["response_due_at"].tzinfo == UTC


@pytest.mark.django_db
def test_sync_create_update_preserves_state() -> None:
    client = FakeClient()
    case, created = sync_one(client, "PP-D-27803")
    assert created and case.source == "dispute" and case.state == "new"
    assert case.paypal_snapshot == load_fixture()
    case.state = "ready"
    case.save()
    client.records[0]["dispute"]["status"] = "RESOLVED"
    client.records[0]["dispute"]["dispute_amount"]["value"] = "100.00"
    updated, created = sync_one(client, "PP-D-27803")
    assert not created and updated.pk == case.pk
    assert updated.state == "ready" and updated.dispute_status == "RESOLVED"
    assert updated.amount == Decimal("100.00")
    updated.state = "new"
    updated.save()
    assert sync_one(client, "PP-D-27803")[0].state == "new"
    assert Case.objects.count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize("identifier,missing", [("CAP-120", "capture"), ("PAYPAL-ORDER-120", "order")])
def test_sync_partial_failure(identifier: str, missing: str) -> None:
    client = FakeClient()
    error = PayPalError(404, "NOT_FOUND", "Unavailable")
    client.failures[identifier] = error
    case, created = sync_one(client, "PP-D-27803")
    assert created and case.paypal_snapshot[missing] is None
    assert str(error) in case.paypal_snapshot["snapshot_errors"][0]
    assert case.paypal_capture_id == "CAP-120"
    if missing == "capture":
        assert case.paypal_snapshot["order"] is None
        assert not any(kind == "order" for kind, _ in client.calls)
    else:
        assert case.paypal_order_id == "PAYPAL-ORDER-120"


@pytest.mark.django_db
def test_sync_collects_errors_and_continues() -> None:
    client = FakeClient()
    existing, _ = sync_one(client, "PP-D-27803")
    error = PayPalError(500, "INTERNAL_ERROR", "Try later")
    client.failures["PP-D-27804"] = error
    result = sync_disputes(client, dispute_state="OPEN")
    assert result.updated == [existing.pk]
    assert len(result.created) == 1
    assert Case.objects.get(pk=result.created[0]).paypal_dispute_id == "PP-D-MISSING"
    assert result.errors == [f"PP-D-27804: {error}"]
    assert ("list", "OPEN") in client.calls


@pytest.mark.django_db
def test_command_batch_and_single() -> None:
    client = FakeClient()
    with patch("cases.management.commands.sync_disputes.PayPalClient.from_env", return_value=client):
        output = StringIO()
        call_command("sync_disputes", state="OPEN", stdout=output)
        assert output.getvalue() == "created 3, updated 0\n"
        assert ("list", "OPEN") in client.calls
        assert client.closed
        output = StringIO()
        call_command("sync_disputes", dispute_id="PP-D-27803", stdout=output)
        assert output.getvalue() == "created 0, updated 1\n"


@pytest.mark.django_db
@pytest.mark.parametrize("single", [False, True])
def test_command_errors(single: bool) -> None:
    client = FakeClient()
    error = PayPalError(403, "FORBIDDEN", "No access")
    client.failures["PP-D-27803"] = error
    output, errors = StringIO(), StringIO()
    with patch("cases.management.commands.sync_disputes.PayPalClient.from_env", return_value=client):
        with pytest.raises(CommandError) as raised:
            call_command("sync_disputes", dispute_id="PP-D-27803" if single else None, stdout=output, stderr=errors)
    assert raised.value.returncode == 1
    assert errors.getvalue() == f"PP-D-27803: {error}\n"
    assert output.getvalue() == ("created 0, updated 0\n" if single else "created 2, updated 0\n")
    assert client.closed
