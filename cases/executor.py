"""Approval-gated, audited execution of PayPal sandbox actions."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from paypal_client import PayPalClient, PayPalError
from paypal_client.client import money
from paypal_client.errors import safe_text

from .guardrails import MONEY_ACTIONS, check_recommendation
from .models import Case, Decision, PayPalAction, Recommendation
from .paypal_record import counted_refunds


class ExecutionError(Exception):
    """An approval, preflight or PayPal execution failed."""

    def __init__(self, message: str, *, violations: list[str] | None = None) -> None:
        super().__init__(message)
        self.violations = violations if violations is not None else [message]


def decide(
    case: Case,
    recommendation: Recommendation,
    outcome: str,
    *,
    action: str | None = None,
    amount: Decimal | None = None,
    note: str = "",
    decided_by: str = "merchant",
) -> Decision:
    if outcome not in Decision.Outcome.values:
        raise ExecutionError("The decision outcome is not recognized")
    with transaction.atomic():
        locked = Case.objects.select_for_update().get(pk=case.pk)
        if locked.state != Case.State.READY:
            raise ExecutionError("The case must be ready for review")
        recommendation = Recommendation.objects.get(pk=recommendation.pk)
        if recommendation.case_id != locked.pk:
            raise ExecutionError("The recommendation does not belong to this case")
        if outcome == Decision.Outcome.REJECTED:
            if not note.strip():
                raise ExecutionError("Rejecting a recommendation requires a reason")
            final_action, final_amount = "", None
            state = Case.State.REJECTED
        else:
            if outcome == Decision.Outcome.APPROVED:
                final_action, final_amount = recommendation.action, recommendation.amount
                note = note or recommendation.buyer_note
            else:
                final_action = action if action is not None else recommendation.action
                final_amount = amount
                if (final_action, final_amount, note) == (
                    recommendation.action, recommendation.amount, recommendation.buyer_note
                ):
                    raise ExecutionError("An edited decision must change the action, amount or note")
            report = check_recommendation(
                action=final_action,
                amount=final_amount,
                currency=recommendation.currency,
                case=locked,
                citations=recommendation.citations,
                evidence_ids=set(locked.evidence.values_list("pk", flat=True)),
                refundable=None,
                now=timezone.now(),
            )
            if not report.ok:
                raise ExecutionError("; ".join(report.violations), violations=report.violations)
            state = Case.State.APPROVED
        decision = Decision.objects.create(
            case=locked, recommendation=recommendation, outcome=outcome,
            action=final_action, amount=final_amount, note=note, decided_by=decided_by,
        )
        if not Case.objects.filter(pk=locked.pk, state=Case.State.READY).update(
            state=state, updated_at=timezone.now()
        ):
            raise ExecutionError("The case is no longer ready for review")
    return decision


def _claim_execution(decision: Decision) -> tuple[Decision, Case, PayPalAction, bool]:
    with transaction.atomic():
        # Lock the case first, consistently with decide(), to serialize workers.
        case = Case.objects.select_for_update().get(pk=decision.case_id)
        decision = Decision.objects.select_related("recommendation").get(pk=decision.pk)
        if decision.case_id != case.pk or decision.recommendation.case_id != case.pk:
            raise ExecutionError("The decision and recommendation must belong to this case")
        if decision.outcome not in {Decision.Outcome.APPROVED, Decision.Outcome.EDITED}:
            raise ExecutionError("Only an approved or edited decision may be executed")
        actions = PayPalAction.objects.filter(decision=decision)
        existing = actions.filter(status=PayPalAction.Status.OK).order_by("pk").first()
        if existing is not None:
            return decision, case, existing, False
        pending = actions.filter(status=PayPalAction.Status.PENDING).order_by("pk").first()
        if case.state == Case.State.EXECUTING and pending is not None:
            return decision, case, pending, False
        if case.state != Case.State.APPROVED or pending is not None:
            raise ExecutionError("The case must be approved and not already executing")
        if decision.action not in Recommendation.Action.values:
            raise ExecutionError("The decision action is not recognized")
        kind = "none" if decision.action == "NEEDS_INFO" else decision.action.lower()
        key = f"{decision.id.hex}-{kind}"
        retries = actions.filter(status=PayPalAction.Status.FAILED).count()
        if retries:
            key += f"-retry{retries}"
        execution = PayPalAction.objects.create(
            case=case, decision=decision, kind=kind, idempotency_key=key,
            paypal_ids={"dispute_id": case.paypal_dispute_id, "capture_id": case.paypal_capture_id},
        )
        if not Case.objects.filter(pk=case.pk, state=Case.State.APPROVED).update(
            state=Case.State.EXECUTING, updated_at=timezone.now()
        ):
            raise ExecutionError("Another worker has already claimed this case")
    return decision, case, execution, True


def _allowed_actions(dispute: dict[str, Any]) -> list[str]:
    options = dispute.get("allowed_response_options")
    if options is not None:
        return [
            action for key, action in (
                ("accept_claim", "ACCEPT_CLAIM"), ("make_offer", "MAKE_OFFER"),
                ("provide_evidence", "PROVIDE_EVIDENCE"),
            ) if key in options
        ]
    rels = {link.get("rel") for link in dispute.get("links", [])}
    return [
        action for rel, action in (
            ("accept-claim", "ACCEPT_CLAIM"), ("make-offer", "MAKE_OFFER"),
            ("provide-evidence", "PROVIDE_EVIDENCE"),
        ) if rel in rels
    ]


def _decimal_amount(payload: dict[str, Any], currency: str) -> Decimal:
    if payload.get("currency_code") != currency:
        raise ExecutionError("PayPal returned a different currency")
    value = Decimal(payload["value"])
    if not value.is_finite() or value < 0:
        raise ExecutionError("PayPal returned an invalid amount")
    return value


def _preflight(
    decision: Decision, case: Case, client: PayPalClient, now: datetime | None
) -> None:
    # Refresh local facts as well as PayPal facts; callers may hold stale model objects.
    case.refresh_from_db()
    refundable = None
    if decision.action != "NEEDS_INFO":
        refundable = _read_paypal_facts(case, client)
    recommendation = Recommendation.objects.get(pk=decision.recommendation_id)
    decision.recommendation = recommendation
    report = check_recommendation(
        action=decision.action, amount=decision.amount, currency=recommendation.currency,
        case=case, citations=recommendation.citations,
        evidence_ids=set(case.evidence.values_list("pk", flat=True)),
        refundable=refundable, now=now if now is not None else timezone.now(),
    )
    if not report.ok:
        raise ExecutionError("; ".join(report.violations), violations=report.violations)


def _read_paypal_facts(case: Case, client: PayPalClient) -> Decimal:
    if case.paypal_dispute_id:
        dispute = client.get_dispute(case.paypal_dispute_id)
        case.allowed_actions = _allowed_actions(dispute)
        if dispute.get("dispute_amount"):
            case.amount = _decimal_amount(dispute["dispute_amount"], case.currency)
        if dispute.get("seller_response_due_date"):
            deadline = parse_datetime(dispute["seller_response_due_date"])
            if deadline is None or timezone.is_naive(deadline):
                raise ExecutionError("PayPal returned an invalid response deadline")
            if case.response_due_at is None or deadline < case.response_due_at:
                case.response_due_at = deadline
    elif case.source == Case.Source.DISPUTE:
        raise ExecutionError("The case has no PayPal dispute ID")
    if not case.paypal_capture_id:
        raise ExecutionError("The case has no PayPal capture ID")
    capture = client.get_capture(case.paypal_capture_id)
    order_id = case.paypal_order_id or (
        (capture.get("supplementary_data") or {}).get("related_ids") or {}
    ).get("order_id")
    if not order_id:
        raise ExecutionError("The capture has no PayPal order ID")
    order = client.get_order(order_id)
    refundable = _decimal_amount(capture["amount"], case.currency)
    for refund in counted_refunds(order):  # a complaint hold is not a refund (see cases/paypal_record.py)
        refundable -= _decimal_amount(refund["amount"], case.currency)
    if capture.get("status") == "REFUNDED":
        refundable = Decimal("0")
    return refundable


def _evidences(note: str) -> list[dict[str, str]]:
    return [{"evidence_type": "OTHER", "notes": note[:2000]}]


def _write_request(
    decision: Decision, case: Case, client: PayPalClient, reason: str
) -> tuple[Any, str, dict[str, Any], dict[str, Any]]:
    note = decision.note
    currency = decision.recommendation.currency
    if decision.action == "ACCEPT_CLAIM":
        fields = {"note": note, "accept_claim_type": "REFUND", "accept_claim_reason": reason}
        return client.accept_claim, case.paypal_dispute_id, fields, fields.copy()
    if decision.action == "MAKE_OFFER":
        fields = {
            "note": note, "offer_type": "REFUND", "amount": decision.amount,
            "currency": currency, "invoice_id": case.order_ref or None,
        }
        payload = {"note": note, "offer_type": "REFUND", "offer_amount": money(decision.amount, currency)}
        if case.order_ref:
            payload["invoice_id"] = case.order_ref
        return client.make_offer, case.paypal_dispute_id, fields, payload
    if decision.action == "PROVIDE_EVIDENCE":
        fields = {"evidences": _evidences(note), "note": note}
        return client.provide_evidence, case.paypal_dispute_id, fields, fields.copy()
    if decision.action == "REFUND":
        fields = {
            "amount": decision.amount, "currency": currency, "note_to_payer": note,
            "invoice_id": None,
        }
        payload = {"amount": money(decision.amount, currency), "note_to_payer": note}
        return client.refund_capture, case.paypal_capture_id, fields, payload
    raise ExecutionError("The decision action is not executable")


def _fail(execution: PayPalAction, response: dict, state: str, http_status: int | None = None) -> None:
    with transaction.atomic():
        execution.status = PayPalAction.Status.FAILED
        execution.response = response
        execution.http_status = http_status
        execution.save(update_fields=["status", "response", "http_status"])
        Case.objects.filter(pk=execution.case_id, state=Case.State.EXECUTING).update(
            state=state, updated_at=timezone.now()
        )


def _verify(decision: Decision, case: Case, execution: PayPalAction, client: PayPalClient) -> None:
    summary: dict[str, Any] = {}
    case_fields: dict[str, Any] = {}
    try:
        if case.paypal_dispute_id:
            dispute = client.get_dispute(case.paypal_dispute_id)
            for source, target in (
                ("status", "dispute_status"), ("dispute_life_cycle_stage", "dispute_stage"),
                ("dispute_outcome", "dispute_outcome"),
            ):
                if source in dispute:
                    summary[target] = dispute[source]
            if "status" in dispute:
                case_fields["dispute_status"] = dispute["status"]
            if "dispute_life_cycle_stage" in dispute:
                case_fields["stage"] = dispute["dispute_life_cycle_stage"]
        if decision.action in MONEY_ACTIONS:
            refund_id = execution.paypal_ids.get("refund_id")
            if refund_id:
                summary["refund_status"] = client.get_refund(refund_id).get("status")
            else:
                summary["capture_status"] = client.get_capture(case.paypal_capture_id).get("status")
    except Exception as exc:
        # The write already succeeded; verification failure must never trigger a retry.
        summary = {"error": safe_text(exc)}
    with transaction.atomic():
        execution.verification = summary
        execution.verified_at = timezone.now()
        execution.save(update_fields=["verification", "verified_at"])
        Case.objects.filter(pk=case.pk, state=Case.State.EXECUTING).update(
            state=Case.State.RESOLVED, updated_at=timezone.now(), **case_fields
        )


def execute_decision(
    decision: Decision,
    client: PayPalClient,
    *,
    now: datetime | None = None,
    accept_claim_reason: str = "COMPANY_POLICY",
) -> PayPalAction:
    decision, case, execution, claimed = _claim_execution(decision)
    if not claimed:
        return execution
    try:
        _preflight(decision, case, client, now)
    except PayPalError as exc:
        _fail_paypal(execution, exc)
        raise ExecutionError(safe_text(exc)) from exc
    except Exception as exc:
        violations = exc.violations if isinstance(exc, ExecutionError) else [safe_text(exc)]
        _fail(execution, {"violations": violations}, Case.State.READY)
        raise ExecutionError(safe_text(exc), violations=violations) from exc
    if decision.action == "NEEDS_INFO":
        with transaction.atomic():
            execution.status = PayPalAction.Status.OK
            execution.save(update_fields=["status"])
            Case.objects.filter(pk=case.pk, state=Case.State.EXECUTING).update(
                state=Case.State.NEEDS_INFO, updated_at=timezone.now()
            )
        return execution
    method, resource_id, fields, payload = _write_request(decision, case, client, accept_claim_reason)
    execution.request = payload
    execution.save(update_fields=["request"])
    try:
        response = method(resource_id, **fields, request_id=execution.idempotency_key)
    except PayPalError as exc:
        _fail_paypal(execution, exc)
        raise ExecutionError(safe_text(exc)) from exc
    # Unknown transport failures deliberately retain pending/executing: the write
    # may have reached PayPal, so using a new request ID could move money twice.
    with transaction.atomic():
        execution.response = response
        execution.status = PayPalAction.Status.OK
        refund_id = response.get("refund_id") or response.get("id")
        if refund_id:
            execution.paypal_ids["refund_id"] = refund_id
        execution.save(update_fields=["response", "status", "paypal_ids"])
    _verify(decision, case, execution, client)
    return execution


def _fail_paypal(execution: PayPalAction, exc: PayPalError) -> None:
    _fail(
        execution,
        {key: getattr(exc, key) for key in ("status", "name", "message", "debug_id", "details")},
        Case.State.APPROVED,
        exc.status,
    )
