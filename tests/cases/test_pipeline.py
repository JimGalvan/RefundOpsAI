"""The chain the Codex modules were built to fit: intake -> record text -> decision -> execution -> verification.

`sync` (C5) and the executor (C4) were written separately from one written contract. These tests run them together
against PayPal-shaped payloads, with only the HTTP edge faked.
"""

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from cases.executor import decide, execute_decision
from cases.models import (
    Case,
    Decision,
    Evidence,
    Investigation,
    PayPalAction,
    Recommendation,
)
from cases.paypal_record import build_paypal_record
from cases.sync import sync_one
from paypal_client import PayPalClient

FIXTURES = Path(__file__).parent / "fixtures"


class PayPalSandboxDouble:
    """Serves the fixture reads and records the writes. A write changes what the next read returns, like PayPal."""

    extract_order_id = staticmethod(PayPalClient.extract_order_id)

    def __init__(self, name: str) -> None:
        self.records = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
        self.writes: list[tuple[str, dict]] = []

    def get_dispute(self, dispute_id):
        return deepcopy(self.records["dispute"])

    def get_capture(self, capture_id):
        return deepcopy(self.records["capture"])

    def get_order(self, order_id):
        return deepcopy(self.records["order"])

    def get_refund(self, refund_id):
        return {"id": refund_id, "status": "COMPLETED"}

    def accept_claim(self, dispute_id, **fields):
        self.writes.append(("accept_claim", fields))
        self.records["dispute"]["status"] = "RESOLVED"
        self.records["dispute"]["dispute_outcome"] = {"outcome_code": "RESOLVED_BUYER_FAVOUR"}
        return {"links": []}

    def make_offer(self, dispute_id, **fields):
        self.writes.append(("make_offer", fields))
        self.records["dispute"]["status"] = "WAITING_FOR_BUYER_RESPONSE"
        return {"links": []}

    def provide_evidence(self, dispute_id, **fields):
        self.writes.append(("provide_evidence", fields))
        return {}


def ready_case(client: PayPalSandboxDouble, action: str, amount: str | None) -> Case:
    """Intake a dispute for real, then add what the investigator and the recommendation step would have stored."""
    case, created = sync_one(client, client.records["dispute"]["dispute_id"])
    assert created
    investigation = Investigation.objects.create(case=case, status=Investigation.Status.COMPLETED)
    fact = Evidence.objects.create(
        case=case, investigation=investigation, kind="tracking", claim="Returned to sender.",
        quote="Returned to sender", source_label="Meridian Parcel",
    )
    Recommendation.objects.create(
        case=case, investigation=investigation, action=action, amount=Decimal(amount) if amount else None,
        currency=case.currency, headline="h", rationale="r", buyer_note="We are sorry.", citations=[fact.pk],
        guardrails={"ok": True}, confidence="high",
    )
    Case.objects.filter(pk=case.pk).update(state=Case.State.READY)
    case.refresh_from_db()
    return case


@pytest.mark.django_db
def test_intake_gives_the_executor_everything_it_checks_and_the_agent_a_quotable_record():
    client = PayPalSandboxDouble("intake_120")
    case, _ = sync_one(client, client.records["dispute"]["dispute_id"])
    assert case.allowed_actions == ["ACCEPT_CLAIM", "MAKE_OFFER", "PROVIDE_EVIDENCE"]
    assert case.paypal_capture_id and case.paypal_order_id and case.order_ref
    record = build_paypal_record(case)
    assert f"Dispute ID: {case.paypal_dispute_id}" in record
    assert f"Store order number (invoice_id): {case.order_ref}" in record


@pytest.mark.django_db
def test_full_refund_runs_through_and_is_verified_against_what_paypal_now_says():
    client = PayPalSandboxDouble("intake_120")
    case = ready_case(client, "ACCEPT_CLAIM", "120.00")
    recommendation = case.recommendations.get()

    decision = decide(case, recommendation, Decision.Outcome.APPROVED)
    action = execute_decision(decision, client)

    assert [name for name, _ in client.writes] == ["accept_claim"]
    assert client.writes[0][1]["accept_claim_type"] == "REFUND"
    case.refresh_from_db()
    assert case.state == Case.State.RESOLVED
    assert action.status == PayPalAction.Status.OK
    assert action.verification["dispute_status"] == "RESOLVED"
    assert case.dispute_status == "RESOLVED"


@pytest.mark.django_db
def test_goodwill_offer_sends_the_amount_and_leaves_the_dispute_open_for_the_buyer():
    client = PayPalSandboxDouble("intake_90")
    case = ready_case(client, "MAKE_OFFER", "25.00")
    decision = decide(case, case.recommendations.get(), Decision.Outcome.APPROVED)
    action = execute_decision(decision, client)

    name, fields = client.writes[0]
    assert name == "make_offer" and fields["amount"] == Decimal("25.00") and fields["offer_type"] == "REFUND"
    assert action.verification["dispute_status"] == "WAITING_FOR_BUYER_RESPONSE"


@pytest.mark.django_db
def test_an_offer_larger_than_the_dispute_never_reaches_paypal():
    client = PayPalSandboxDouble("intake_90")
    case = ready_case(client, "MAKE_OFFER", "25.00")
    with pytest.raises(Exception, match="exceeds the case amount"):
        decide(case, case.recommendations.get(), Decision.Outcome.EDITED, amount=Decimal("500.00"), note="edited")
    assert client.writes == []


@pytest.mark.django_db
def test_an_earlier_refund_reduces_what_can_still_be_refunded_at_execution_time():
    client = PayPalSandboxDouble("intake_120")
    case = ready_case(client, "ACCEPT_CLAIM", "120.00")
    decision = decide(case, case.recommendations.get(), Decision.Outcome.APPROVED)
    # Between approval and execution someone refunds $25 from PayPal directly.
    client.records["order"]["purchase_units"][0].setdefault("payments", {})["refunds"] = [
        {"id": "R1", "status": "COMPLETED", "amount": {"currency_code": "USD", "value": "25.00"}}
    ]
    with pytest.raises(Exception, match="refundable|at most"):
        execute_decision(decision, client)
    assert client.writes == []
    case.refresh_from_db()
    assert case.state == Case.State.READY
