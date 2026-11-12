"""Pure validation of proposed merchant actions."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .models import Case

MONEY_ACTIONS = frozenset({"ACCEPT_CLAIM", "MAKE_OFFER", "REFUND"})
DISPUTE_ACTIONS = frozenset({"ACCEPT_CLAIM", "MAKE_OFFER", "PROVIDE_EVIDENCE"})
ACTIONS = MONEY_ACTIONS | DISPUTE_ACTIONS | {"NEEDS_INFO"}


@dataclass(frozen=True)
class GuardrailReport:
    ok: bool
    violations: list[str]
    warnings: list[str]

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "violations": list(self.violations),
            "warnings": list(self.warnings),
        }


def check_recommendation(
    *,
    action: str,
    amount: Decimal | None,
    currency: str,
    case: Case,
    citations: list[int],
    evidence_ids: set[int],
    refundable: Decimal | None,
    now: datetime,
) -> GuardrailReport:
    violations: list[str] = []
    warnings: list[str] = []
    if action not in ACTIONS:
        violations.append("The recommended action is not recognized")
    if action == "REFUND" and case.source != "complaint":
        violations.append("Direct refunds are only allowed for complaints")
    if action in DISPUTE_ACTIONS:
        if case.source != "dispute":
            violations.append("This action is only allowed for disputes")
        if action not in case.allowed_actions:
            violations.append("PayPal does not currently allow this action")

    if action in MONEY_ACTIONS:
        if amount is None:
            violations.append("A money action requires an amount")
        elif not amount.is_finite():
            violations.append("The amount must be a finite number")
        else:
            if amount <= 0:
                violations.append("The amount must be greater than zero")
            if amount.as_tuple().exponent < -2:
                violations.append("The amount must have at most two decimal places")
            if amount > case.amount:
                violations.append("The amount exceeds the case amount")
            if refundable is not None and amount > refundable:
                violations.append("The amount exceeds the remaining refundable amount")
            if action == "ACCEPT_CLAIM" and amount != case.amount:
                violations.append("Accepting a claim requires the full dispute amount")
            if action == "MAKE_OFFER" and amount > case.amount * Decimal("0.30"):
                warnings.append("The offer exceeds 30% of the case amount")
        if currency != case.currency:
            violations.append("The currency must match the case currency")
        if len(set(citations)) < 2:
            warnings.append("Fewer than two distinct evidence items support this money action")
    elif action in {"PROVIDE_EVIDENCE", "NEEDS_INFO"} and amount is not None:
        violations.append("This action must not include an amount")

    if action != "NEEDS_INFO" and not citations:
        violations.append("At least one evidence citation is required")
    for citation in citations:
        if citation not in evidence_ids:
            violations.append(f"Evidence citation {citation} does not belong to this case")
    if case.response_due_at is not None and now > case.response_due_at:
        violations.append("The response deadline has passed")
    return GuardrailReport(not violations, violations, warnings)
