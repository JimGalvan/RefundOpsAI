import json
from copy import deepcopy
from io import StringIO
from unittest.mock import Mock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from cases.models import Case, Decision, PayPalAction
from paypal_client import PayPalError

pytestmark = pytest.mark.django_db


def make_case() -> Case:
    return Case.objects.create(
        amount="120.00", currency="USD", state="ready", order_ref="ORD-10482",
        paypal_dispute_id="PP-TEST", paypal_capture_id="CAP-TEST", paypal_order_id="ORDER-TEST",
        paypal_snapshot={
            "dispute": {"status": "WAITING_FOR_SELLER_RESPONSE"},
            "capture": {"id": "CAP-TEST", "status": "PENDING", "status_details": {"reason": "BUYER_COMPLAINT"}},
            "order": {"purchase_units": [{"payments": {"refunds": [{
                "id": "REF-TEST", "amount": {"value": "115.32", "currency_code": "USD"},
                "status": "PENDING", "seller_payable_breakdown": {"total_refunded_amount": {"value": "0.00"}},
            }]}}]},
        },
    )


def test_cached_inspection_preserves_state_and_never_opens_client(tmp_path, settings):
    case = make_case()
    before = Case.objects.values().get(pk=case.pk)
    settings.BASE_DIR = tmp_path
    output = StringIO()
    with patch("cases.management.commands.inspect_paypal.PayPalClient.from_env") as factory:
        call_command("inspect_paypal", case.pk, cached=True, save=True, stdout=output)
    factory.assert_not_called()
    assert Case.objects.values().get(pk=case.pk) == before
    assert not Decision.objects.exists() and not PayPalAction.objects.exists()
    saved, = (tmp_path / "data" / "paypal_checks").glob("*.json")
    report = json.loads(saved.read_text())
    assert report["source"] == "cached snapshots"
    summary = report["cases"][0]["summary"]
    assert summary["capture_pending_reason"] == "BUYER_COMPLAINT"
    assert summary["refunds_counted_by_current_guardrails"] == "115.32"
    assert summary["refunds"][0]["total_refunded_amount"] == {"value": "0.00"}
    assert len(summary["warnings"]) == 2


def test_live_inspection_reads_individual_refunds_and_does_not_mutate_case():
    case = make_case()
    before = Case.objects.values().get(pk=case.pk)
    client = Mock()
    for kind in ("dispute", "capture", "order"):
        getattr(client, f"get_{kind}").return_value = deepcopy(case.paypal_snapshot[kind])
    client.get_refund.return_value = {"id": "REF-TEST", "status": "PENDING", "status_details": {"reason": "ECHECK"}}
    output = StringIO()
    with patch("cases.management.commands.inspect_paypal.PayPalClient.from_env", return_value=client):
        call_command("inspect_paypal", case.pk, stdout=output)
    assert [call[0] for call in client.method_calls] == ["get_dispute", "get_capture", "get_order", "get_refund", "close"]
    client.get_refund.assert_called_once_with("REF-TEST")
    assert '"reason": "ECHECK"' in output.getvalue()
    assert Case.objects.values().get(pk=case.pk) == before
    assert not Decision.objects.exists() and not PayPalAction.objects.exists()


def test_failed_refund_read_is_reported_and_saved_with_other_responses(tmp_path, settings):
    case = make_case()
    client = Mock()
    for kind in ("dispute", "capture", "order"):
        getattr(client, f"get_{kind}").return_value = case.paypal_snapshot[kind]
    client.get_refund.side_effect = PayPalError(404, "RESOURCE_NOT_FOUND", "Refund not found", "DEBUG-TEST")
    settings.BASE_DIR = tmp_path
    output = StringIO()
    with patch("cases.management.commands.inspect_paypal.PayPalClient.from_env", return_value=client):
        with pytest.raises(CommandError, match="Some PayPal reads failed"):
            call_command("inspect_paypal", case.pk, save=True, stdout=output)
    client.close.assert_called_once()
    saved, = (tmp_path / "data" / "paypal_checks").glob("*.json")
    report = json.loads(saved.read_text())
    assert "404 RESOURCE_NOT_FOUND" in report["cases"][0]["reads"]["errors"]["refund:REF-TEST"]
    assert report["cases"][0]["reads"]["order"] == case.paypal_snapshot["order"]


def test_missing_or_offline_case_is_rejected_before_creating_client():
    case = make_case()
    case.paypal_dispute_id = "DEMO-A"
    case.save()
    with patch("cases.management.commands.inspect_paypal.PayPalClient.from_env") as factory:
        with pytest.raises(CommandError, match="Cases not found"):
            call_command("inspect_paypal", case.pk, 99999)
        with pytest.raises(CommandError, match="Offline demo cases"):
            call_command("inspect_paypal", case.pk)
    factory.assert_not_called()
