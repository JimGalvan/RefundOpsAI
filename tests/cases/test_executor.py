from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from threading import Event
from typing import Any

import pytest
from django.db import close_old_connections
from django.utils import timezone

from cases.executor import ExecutionError, decide, execute_decision
from cases.models import (
    Case,
    Decision,
    Evidence,
    Investigation,
    PayPalAction,
    Recommendation,
)
from paypal_client import PayPalError

pytestmark = pytest.mark.django_db


class FakePayPalClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.dispute = {
            "status": "WAITING_FOR_SELLER_RESPONSE", "dispute_life_cycle_stage": "INQUIRY",
            "allowed_response_options": {"accept_claim": {}, "make_offer": {}, "provide_evidence": {}},
        }
        self.verified_dispute = {
            "status": "RESOLVED", "dispute_life_cycle_stage": "CHARGEBACK",
            "dispute_outcome": {"outcome_code": "RESOLVED_BUYER_FAVOUR"},
        }
        self.capture = {
            "status": "COMPLETED", "amount": {"value": "100.00", "currency_code": "USD"},
            "supplementary_data": {"related_ids": {"order_id": "ORDER-1"}},
        }
        self.order = {"purchase_units": [{"payments": {"refunds": []}}]}
        self.response: dict = {"id": "REFUND-1", "status": "COMPLETED"}
        self.write_error: Exception | None = None
        self.verify_error: Exception | None = None
        self.read_error: Exception | None = None
        self.on_preflight: Callable[[], None] | None = None
        self.wrote = False

    @property
    def writes(self) -> list[tuple[str, tuple, dict]]:
        return [call for call in self.calls if not call[0].startswith("get_")]

    def get_dispute(self, dispute_id: str) -> dict:
        self.calls.append(("get_dispute", (dispute_id,), {}))
        if self.read_error:
            raise self.read_error
        if self.wrote:
            if self.verify_error:
                raise self.verify_error
            return deepcopy(self.verified_dispute)
        if self.on_preflight:
            self.on_preflight()
        return deepcopy(self.dispute)

    def get_capture(self, capture_id: str) -> dict:
        self.calls.append(("get_capture", (capture_id,), {}))
        if self.wrote and self.verify_error:
            raise self.verify_error
        return deepcopy(self.capture)

    def get_order(self, order_id: str) -> dict:
        self.calls.append(("get_order", (order_id,), {}))
        return deepcopy(self.order)

    def get_refund(self, refund_id: str) -> dict:
        self.calls.append(("get_refund", (refund_id,), {}))
        if self.verify_error:
            raise self.verify_error
        return {"id": refund_id, "status": "COMPLETED"}

    def _write(self, name: str, resource_id: str, fields: dict) -> dict:
        self.calls.append((name, (resource_id,), fields))
        if self.write_error:
            raise self.write_error
        self.wrote = True
        return deepcopy(self.response)

    def accept_claim(
        self, dispute_id: str, *, note: str, accept_claim_type: str,
        accept_claim_reason: str, request_id: str,
    ) -> dict:
        return self._write("accept_claim", dispute_id, {
            "note": note, "accept_claim_type": accept_claim_type,
            "accept_claim_reason": accept_claim_reason, "request_id": request_id,
        })

    def make_offer(
        self, dispute_id: str, *, note: str, offer_type: str, amount: Decimal,
        currency: str, invoice_id: str | None, request_id: str,
    ) -> dict:
        return self._write("make_offer", dispute_id, {
            "note": note, "offer_type": offer_type, "amount": amount,
            "currency": currency, "invoice_id": invoice_id, "request_id": request_id,
        })

    def provide_evidence(
        self, dispute_id: str, *, evidences: list[dict], note: str, request_id: str,
    ) -> dict:
        return self._write("provide_evidence", dispute_id, {
            "evidences": evidences, "note": note, "request_id": request_id,
        })

    def refund_capture(
        self, capture_id: str, *, amount: Decimal, currency: str,
        note_to_payer: str, invoice_id: str | None, request_id: str,
    ) -> dict:
        return self._write("refund_capture", capture_id, {
            "amount": amount, "currency": currency, "note_to_payer": note_to_payer,
            "invoice_id": invoice_id, "request_id": request_id,
        })


def make_case(action: str = "MAKE_OFFER") -> tuple[Case, Recommendation]:
    complaint = action == "REFUND"
    case = Case.objects.create(
        source="complaint" if complaint else "dispute", state="ready",
        amount=Decimal("100"), currency="USD", paypal_capture_id="CAPTURE-1",
        paypal_dispute_id="" if complaint else "DISPUTE-1", order_ref="INV-1",
        allowed_actions=["ACCEPT_CLAIM", "MAKE_OFFER", "PROVIDE_EVIDENCE"],
        response_due_at=timezone.now() + timedelta(days=1),
    )
    investigation = Investigation.objects.create(case=case)
    evidence = [
        Evidence.objects.create(case=case, investigation=investigation, claim="Delivered", quote="Delivered")
        for _ in range(2)
    ]
    amount = Decimal("100") if action == "ACCEPT_CLAIM" else Decimal("20")
    recommendation = Recommendation.objects.create(
        case=case, investigation=investigation, action=action,
        amount=amount if action not in {"PROVIDE_EVIDENCE", "NEEDS_INFO"} else None,
        currency="USD", headline="Recommendation", rationale="Evidence supports it",
        buyer_note="We reviewed your order.", citations=[item.pk for item in evidence],
    )
    return case, recommendation


def approved(action: str = "MAKE_OFFER") -> Decision:
    case, recommendation = make_case(action)
    return decide(case, recommendation, "approved")


def test_decide_approved_copies_recommendation_and_ignores_action_overrides() -> None:
    case, recommendation = make_case()
    decision = decide(case, recommendation, "approved", action="REFUND", amount=Decimal("99"))
    assert decision.action == recommendation.action
    assert decision.amount == recommendation.amount
    assert decision.note == recommendation.buyer_note
    assert decision.decided_by == "merchant"
    case.refresh_from_db()
    assert case.state == "approved"


def test_decide_edited() -> None:
    case, recommendation = make_case()
    decision = decide(
        case, recommendation, "edited", action="ACCEPT_CLAIM",
        amount=Decimal("100"), note="Full refund", decided_by="owner",
    )
    assert (decision.action, decision.amount, decision.note) == ("ACCEPT_CLAIM", Decimal("100"), "Full refund")
    assert decision.outcome == "edited"
    assert decision.decided_by == "owner"
    case.refresh_from_db()
    assert case.state == "approved"


def test_decide_edit_to_nonmoney_action() -> None:
    case, recommendation = make_case()
    decision = decide(case, recommendation, "edited", action="PROVIDE_EVIDENCE", note="Delivery receipt")
    assert decision.amount is None


def test_decide_rejected() -> None:
    case, recommendation = make_case()
    decision = decide(case, recommendation, "rejected", note="Tracking is incomplete")
    assert decision.note == "Tracking is incomplete"
    assert decision.action == ""
    assert decision.amount is None
    case.refresh_from_db()
    assert case.state == "rejected"


@pytest.mark.parametrize("note", ["", "  \n"])
def test_rejection_needs_a_reason(note: str) -> None:
    case, recommendation = make_case()
    with pytest.raises(ExecutionError, match="requires a reason"):
        decide(case, recommendation, "rejected", note=note)
    assert not Decision.objects.exists()
    case.refresh_from_db()
    assert case.state == "ready"


@pytest.mark.parametrize("state", ["new", "investigating", "approved", "executing", "resolved", "rejected", "needs_info"])
def test_decide_wrong_state_uses_database_state(state: str) -> None:
    case, recommendation = make_case()
    Case.objects.filter(pk=case.pk).update(state=state)
    with pytest.raises(ExecutionError, match="ready"):
        decide(case, recommendation, "approved")
    assert not Decision.objects.exists()


def test_decide_identical_edit_rejected() -> None:
    case, recommendation = make_case()
    with pytest.raises(ExecutionError, match="must change"):
        decide(case, recommendation, "edited", action=recommendation.action,
               amount=recommendation.amount, note=recommendation.buyer_note)


def test_decide_checks_violations_and_rolls_back() -> None:
    case, recommendation = make_case()
    recommendation.citations = []
    recommendation.currency = "EUR"
    recommendation.save()
    with pytest.raises(ExecutionError, match="currency must match.*citation"):
        decide(case, recommendation, "approved")
    case.refresh_from_db()
    assert case.state == "ready"
    assert not Decision.objects.exists()


def test_decide_invalid_outcome_and_wrong_case() -> None:
    case, recommendation = make_case()
    with pytest.raises(ExecutionError, match="outcome"):
        decide(case, recommendation, "yes")
    other, _ = make_case()
    with pytest.raises(ExecutionError, match="does not belong"):
        decide(other, recommendation, "approved")


def test_decide_citations_must_belong_to_this_case() -> None:
    case, recommendation = make_case()
    _, other = make_case()
    recommendation.citations = other.citations
    recommendation.save()
    with pytest.raises(ExecutionError, match="does not belong"):
        decide(case, recommendation, "approved")


@pytest.mark.parametrize("action", ["ACCEPT_CLAIM", "MAKE_OFFER", "PROVIDE_EVIDENCE", "REFUND"])
def test_execution_exact_arguments_payload_audit_verification_and_idempotency(action: str) -> None:
    decision = approved(action)
    client = FakePayPalClient()

    def inspect_claim() -> None:
        decision.case.refresh_from_db()
        assert decision.case.state == "executing"
        assert PayPalAction.objects.get(decision=decision).status == "pending"

    client.on_preflight = inspect_claim
    execution = execute_decision(decision, client)
    key = f"{decision.id.hex}-{action.lower()}"
    note = decision.note
    expected_kwargs: dict[str, Any]
    if action == "ACCEPT_CLAIM":
        method, resource = "accept_claim", "DISPUTE-1"
        expected_kwargs = {"note": note, "accept_claim_type": "REFUND", "accept_claim_reason": "COMPANY_POLICY"}
        expected_payload = expected_kwargs.copy()
    elif action == "MAKE_OFFER":
        method, resource = "make_offer", "DISPUTE-1"
        expected_kwargs = {"note": note, "offer_type": "REFUND", "amount": Decimal("20"), "currency": "USD", "invoice_id": "INV-1"}
        expected_payload = {"note": note, "offer_type": "REFUND", "offer_amount": {"value": "20.00", "currency_code": "USD"}, "invoice_id": "INV-1"}
    elif action == "PROVIDE_EVIDENCE":
        method, resource = "provide_evidence", "DISPUTE-1"
        expected_kwargs = {"evidences": [{"evidence_type": "OTHER", "notes": note}], "note": note}
        expected_payload = expected_kwargs.copy()
    else:
        method, resource = "refund_capture", "CAPTURE-1"
        expected_kwargs = {"amount": Decimal("20"), "currency": "USD", "note_to_payer": note, "invoice_id": None}
        expected_payload = {"amount": {"value": "20.00", "currency_code": "USD"}, "note_to_payer": note}
    expected_kwargs["request_id"] = key
    assert client.writes == [(method, (resource,), expected_kwargs)]
    execution.refresh_from_db()
    assert execution.kind == action.lower()
    assert execution.idempotency_key == key
    assert execution.request == expected_payload
    assert execution.response == client.response
    assert execution.http_status is None  # The client returns JSON only on success.
    assert execution.status == "ok"
    assert execution.paypal_ids == {
        "dispute_id": "" if action == "REFUND" else "DISPUTE-1",
        "capture_id": "CAPTURE-1", "refund_id": "REFUND-1",
    }
    assert execution.verified_at is not None
    if action != "REFUND":
        assert execution.verification["dispute_status"] == "RESOLVED"
        assert execution.verification["dispute_stage"] == "CHARGEBACK"
        assert execution.verification["dispute_outcome"] == client.verified_dispute["dispute_outcome"]
    if action != "PROVIDE_EVIDENCE":
        assert execution.verification["refund_status"] == "COMPLETED"
    assert ("get_order", ("ORDER-1",), {}) in client.calls
    decision.case.refresh_from_db()
    assert decision.case.state == "resolved"
    if action != "REFUND":
        assert decision.case.dispute_status == "RESOLVED"
        assert decision.case.stage == "CHARGEBACK"
    calls = deepcopy(client.calls)
    assert execute_decision(decision, client).pk == execution.pk
    assert client.calls == calls
    assert PayPalAction.objects.filter(decision=decision).count() == 1


def test_execute_edited_decision() -> None:
    case, recommendation = make_case()
    decision = decide(case, recommendation, "edited", amount=Decimal("10"), note="A smaller offer")
    client = FakePayPalClient()
    execute_decision(decision, client)
    assert client.writes[0][2]["amount"] == Decimal("10")
    assert client.writes[0][2]["note"] == "A smaller offer"


def test_needs_info_is_an_idempotent_local_action() -> None:
    decision = approved("NEEDS_INFO")
    client = FakePayPalClient()
    execution = execute_decision(decision, client)
    assert (execution.kind, execution.status) == ("none", "ok")
    assert execution.request == execution.response == {}
    assert client.calls == []
    decision.case.refresh_from_db()
    assert decision.case.state == "needs_info"
    assert execute_decision(decision, client).pk == execution.pk
    assert client.calls == []


def test_needs_info_still_validates_local_facts_without_paypal_reads() -> None:
    decision = approved("NEEDS_INFO")
    Decision.objects.filter(pk=decision.pk).update(amount=Decimal("1"))
    Case.objects.filter(pk=decision.case_id).update(response_due_at=timezone.now() - timedelta(seconds=1))
    client = FakePayPalClient()
    with pytest.raises(ExecutionError):
        execute_decision(decision, client)
    assert not client.calls
    execution = PayPalAction.objects.get(decision=decision)
    assert execution.response["violations"] == [
        "This action must not include an amount", "The response deadline has passed",
    ]
    assert execution.status == "failed"
    decision.case.refresh_from_db()
    assert decision.case.state == "ready"


def test_preflight_checks_clock_after_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    decision = approved()
    start = timezone.now()
    deadline = start + timedelta(seconds=1)
    Case.objects.filter(pk=decision.case_id).update(response_due_at=deadline)
    client = FakePayPalClient()

    def advance_clock() -> None:
        monkeypatch.setattr("cases.executor.timezone.now", lambda: start + timedelta(seconds=2))

    client.on_preflight = advance_clock
    with pytest.raises(ExecutionError, match="deadline has passed"):
        execute_decision(decision, client)
    assert not client.writes


def test_explicit_execution_time_is_used() -> None:
    decision = approved()
    client = FakePayPalClient()
    with pytest.raises(ExecutionError, match="deadline has passed"):
        execute_decision(decision, client, now=timezone.now() + timedelta(days=2))
    assert not client.writes


@pytest.mark.parametrize("state", ["ready", "rejected", "new", "executing", "resolved"])
def test_execute_rejects_wrong_state(state: str) -> None:
    decision = approved()
    Case.objects.filter(pk=decision.case_id).update(state=state)
    client = FakePayPalClient()
    with pytest.raises(ExecutionError, match="approved"):
        execute_decision(decision, client)
    assert not client.calls
    assert not PayPalAction.objects.exists()


def test_execute_rejects_rejected_decision_even_when_case_approved() -> None:
    decision = approved()
    Decision.objects.filter(pk=decision.pk).update(outcome="rejected")
    client = FakePayPalClient()
    with pytest.raises(ExecutionError, match="Only an approved"):
        execute_decision(decision, client)
    assert not client.calls


@pytest.mark.parametrize("stale", ["permissions", "completed_refund", "pending_refund", "refunded_capture", "deadline", "fresh_deadline", "currency", "citations"])
def test_stale_preflight_blocks_all_writes(stale: str) -> None:
    decision = approved()
    client = FakePayPalClient()
    if stale == "permissions":
        client.dispute["allowed_response_options"] = {}
        client.dispute["links"] = [{"rel": "make-offer"}]
    elif stale in {"completed_refund", "pending_refund"}:
        status = "COMPLETED" if stale == "completed_refund" else "PENDING"
        client.order["purchase_units"][0]["payments"]["refunds"] = [{
            "status": status, "amount": {"value": "90.00", "currency_code": "USD"},
        }]
    elif stale == "refunded_capture":
        client.capture["status"] = "REFUNDED"
    elif stale == "deadline":
        Case.objects.filter(pk=decision.case_id).update(response_due_at=timezone.now() - timedelta(seconds=1))
    elif stale == "fresh_deadline":
        client.dispute["seller_response_due_date"] = (timezone.now() - timedelta(seconds=1)).isoformat()
    elif stale == "currency":
        client.capture["amount"]["currency_code"] = "EUR"
    else:
        decision.case.evidence.all().delete()
    with pytest.raises(ExecutionError):
        execute_decision(decision, client)
    assert client.writes == []
    execution = PayPalAction.objects.get(decision=decision)
    assert execution.status == "failed"
    assert execution.response["violations"]
    decision.case.refresh_from_db()
    assert decision.case.state == "ready"
    with pytest.raises(ExecutionError):
        execute_decision(decision, client)
    assert not client.writes


def test_refund_sum_counts_only_completed_and_pending() -> None:
    decision = approved()
    client = FakePayPalClient()
    client.order["purchase_units"][0]["payments"]["refunds"] = [
        {"status": status, "amount": {"value": value, "currency_code": "USD"}}
        for status, value in [("COMPLETED", "40"), ("PENDING", "40"), ("CANCELLED", "100"), ("FAILED", "100")]
    ]
    assert execute_decision(decision, client).status == "ok"


def test_fresh_dispute_amount_is_checked() -> None:
    decision = approved()
    client = FakePayPalClient()
    client.dispute["dispute_amount"] = {"value": "10", "currency_code": "USD"}
    with pytest.raises(ExecutionError, match="case amount"):
        execute_decision(decision, client)
    assert not client.writes


def test_fallback_allowed_actions_from_links_and_case_order_id() -> None:
    decision = approved()
    Case.objects.filter(pk=decision.case_id).update(paypal_order_id="ORDER-EXPLICIT")
    client = FakePayPalClient()
    client.dispute.pop("allowed_response_options")
    client.dispute["links"] = [{"rel": "make-offer"}]
    execute_decision(decision, client)
    assert ("get_order", ("ORDER-EXPLICIT",), {}) in client.calls


@pytest.mark.parametrize("read_error", [False, True])
def test_paypal_error_persisted_safely_and_retry_gets_new_key(read_error: bool) -> None:
    decision = approved()
    client = FakePayPalClient()
    error = PayPalError(
        422, "UNPROCESSABLE_ENTITY", "Try again\nlater", "debug-123",
        [{"issue": "CONFLICT"}], raw={"headers": {"Authorization": "do not store"}},
    )
    if read_error:
        client.read_error = error
    else:
        client.write_error = error
    with pytest.raises(ExecutionError) as caught:
        execute_decision(decision, client)
    assert "\n" not in str(caught.value)
    failed = PayPalAction.objects.get(decision=decision)
    assert failed.status == "failed"
    assert failed.http_status == 422
    assert failed.response == {
        "status": 422, "name": "UNPROCESSABLE_ENTITY", "message": "Try again\nlater",
        "debug_id": "debug-123", "details": [{"issue": "CONFLICT"}],
    }
    assert "Authorization" not in str(failed.response)
    decision.case.refresh_from_db()
    assert decision.case.state == "approved"
    client.read_error = client.write_error = None
    execution = execute_decision(decision, client)
    assert execution.idempotency_key == failed.idempotency_key + "-retry1"
    assert execution.status == "ok"
    assert PayPalAction.objects.count() == 2
    assert len({call[2]["request_id"] for call in client.writes}) == len(client.writes)
    failed.refresh_from_db()
    assert failed.status == "failed"


def test_multiple_failures_get_increasing_retry_keys() -> None:
    decision = approved()
    client = FakePayPalClient()
    client.write_error = PayPalError(503, "UNAVAILABLE", "Try later")
    for _ in range(2):
        with pytest.raises(ExecutionError):
            execute_decision(decision, client)
    client.write_error = None
    execution = execute_decision(decision, client)
    assert execution.idempotency_key.endswith("-retry2")
    assert len({call[2]["request_id"] for call in client.writes}) == 3


@pytest.mark.parametrize("state", ["approved", "executing", "resolved"])
def test_preexisting_ok_action_is_untouched(state: str) -> None:
    decision = approved()
    Case.objects.filter(pk=decision.case_id).update(state=state)
    execution = PayPalAction.objects.create(
        case_id=decision.case_id, decision=decision, kind="make_offer", status="ok",
        idempotency_key=f"{decision.id.hex}-make_offer", request={"original": True},
        response={"kept": True}, verification={"kept": True}, verified_at=timezone.now(),
    )
    before = PayPalAction.objects.values().get(pk=execution.pk)
    client = FakePayPalClient()
    assert execute_decision(decision, client).pk == execution.pk
    assert PayPalAction.objects.values().get(pk=execution.pk) == before
    assert not client.calls
    decision.case.refresh_from_db()
    assert decision.case.state == state


def test_pending_action_is_returned_when_case_is_executing() -> None:
    decision = approved()
    Case.objects.filter(pk=decision.case_id).update(state="executing")
    execution = PayPalAction.objects.create(
        case_id=decision.case_id, decision=decision, kind="make_offer",
        idempotency_key=f"{decision.id.hex}-make_offer",
    )
    client = FakePayPalClient()
    assert execute_decision(decision, client).pk == execution.pk
    assert not client.calls


@pytest.mark.parametrize("action", ["MAKE_OFFER", "REFUND"])
def test_verification_failure_keeps_write_successful(action: str) -> None:
    decision = approved(action)
    client = FakePayPalClient()
    client.verify_error = PayPalError(503, "UNAVAILABLE", "Verification unavailable")
    execution = execute_decision(decision, client)
    assert execution.status == "ok"
    assert "Verification unavailable" in execution.verification["error"]
    assert execution.verified_at is not None
    decision.case.refresh_from_db()
    assert decision.case.state == "resolved"
    execute_decision(decision, client)
    assert len(client.writes) == 1


@pytest.mark.parametrize("response", [{}, {"refund_id": "REFUND-EXPLICIT"}])
def test_refund_verification_uses_capture_or_refund_id(response: dict) -> None:
    decision = approved("ACCEPT_CLAIM")
    client = FakePayPalClient()
    client.response = response
    execution = execute_decision(decision, client, accept_claim_reason="MERCHANDISE_RETURNED")
    assert client.writes[0][2]["accept_claim_reason"] == "MERCHANDISE_RETURNED"
    if response:
        assert execution.paypal_ids["refund_id"] == "REFUND-EXPLICIT"
        assert ("get_refund", ("REFUND-EXPLICIT",), {}) in client.calls
    else:
        assert execution.verification["capture_status"] == "COMPLETED"
        assert len([call for call in client.calls if call[0] == "get_capture"]) == 2


def test_evidence_notes_are_truncated_only_in_evidence_builder() -> None:
    case, recommendation = make_case("PROVIDE_EVIDENCE")
    decision = decide(case, recommendation, "approved", note="x" * 2100)
    client = FakePayPalClient()
    execution = execute_decision(decision, client)
    assert client.writes[0][2]["evidences"] == [{"evidence_type": "OTHER", "notes": "x" * 2000}]
    assert client.writes[0][2]["note"] == "x" * 2100
    assert execution.request["evidences"][0]["notes"] == "x" * 2000


def test_offer_omits_empty_invoice_in_payload() -> None:
    decision = approved()
    Case.objects.filter(pk=decision.case_id).update(order_ref="")
    client = FakePayPalClient()
    execution = execute_decision(decision, client)
    assert client.writes[0][2]["invoice_id"] is None
    assert "invoice_id" not in execution.request


def test_unknown_write_failure_keeps_pending_to_prevent_double_payment() -> None:
    decision = approved()
    client = FakePayPalClient()
    client.write_error = TimeoutError("The write outcome is unknown")
    with pytest.raises(TimeoutError):
        execute_decision(decision, client)
    execution = PayPalAction.objects.get(decision=decision)
    assert execution.status == "pending"
    decision.case.refresh_from_db()
    assert decision.case.state == "executing"
    assert execute_decision(decision, client).pk == execution.pk
    assert len(client.writes) == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_calls_make_exactly_one_write() -> None:
    decision = approved()
    client = FakePayPalClient()
    entered, release = Event(), Event()

    def wait_in_preflight() -> None:
        entered.set()
        assert release.wait(timeout=10)

    client.on_preflight = wait_in_preflight

    def worker() -> int:
        close_old_connections()
        try:
            return execute_decision(Decision.objects.get(pk=decision.pk), client).pk
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(worker)
        try:
            assert entered.wait(timeout=10)
            overlapping = execute_decision(decision, client)
            assert overlapping.status == "pending"
            assert not client.writes
        finally:
            release.set()
        assert first.result(timeout=10) == overlapping.pk
    assert len(client.writes) == 1
    assert PayPalAction.objects.filter(decision=decision).count() == 1
