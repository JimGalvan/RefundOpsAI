from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from cases.guardrails import GuardrailReport, check_recommendation
from cases.models import Case

NOW = datetime(2026, 11, 11, 18, tzinfo=UTC)


def check(**overrides: object) -> GuardrailReport:
    values = {
        "action": "MAKE_OFFER", "amount": Decimal("20.00"), "currency": "USD",
        "case": Case(
            source="dispute", amount=Decimal("100"), currency="USD",
            allowed_actions=["ACCEPT_CLAIM", "MAKE_OFFER", "PROVIDE_EVIDENCE"],
        ),
        "citations": [1, 2], "evidence_ids": {1, 2},
        "refundable": Decimal("100"), "now": NOW,
    }
    values.update(overrides)
    return check_recommendation(**values)


@pytest.mark.parametrize(
    ("action", "amount", "source"),
    [
        ("ACCEPT_CLAIM", "100", "dispute"),
        ("MAKE_OFFER", "20", "dispute"),
        ("PROVIDE_EVIDENCE", None, "dispute"),
        ("REFUND", "10", "complaint"),
        ("NEEDS_INFO", None, "dispute"),
        ("NEEDS_INFO", None, "complaint"),
    ],
)
def test_valid_actions(action: str, amount: str | None, source: str) -> None:
    case = Case(
        source=source, amount=Decimal("100"), currency="USD",
        allowed_actions=["ACCEPT_CLAIM", "MAKE_OFFER", "PROVIDE_EVIDENCE"],
    )
    assert check(action=action, amount=Decimal(amount) if amount else None, case=case).ok


def test_needs_info_needs_no_permission_or_citations() -> None:
    assert check(
        action="NEEDS_INFO", amount=None, citations=[],
        case=Case(source="complaint", amount=Decimal("100"), allowed_actions=[]),
    ).as_dict() == {"ok": True, "violations": [], "warnings": []}


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"action": "DELETE"}, "not recognized"),
        ({"action": "REFUND"}, "only allowed for complaints"),
        ({"amount": None}, "requires an amount"),
        ({"amount": Decimal("0")}, "greater than zero"),
        ({"amount": Decimal("-1")}, "greater than zero"),
        ({"amount": Decimal("1.001")}, "two decimal places"),
        ({"amount": Decimal("1.000")}, "two decimal places"),
        ({"amount": Decimal("101")}, "exceeds the case amount"),
        ({"refundable": Decimal("19.99")}, "remaining refundable amount"),
        ({"currency": "EUR"}, "currency must match"),
        ({"action": "ACCEPT_CLAIM"}, "full dispute amount"),
        ({"action": "PROVIDE_EVIDENCE"}, "must not include an amount"),
        ({"action": "NEEDS_INFO"}, "must not include an amount"),
        ({"citations": []}, "At least one evidence citation"),
        ({"citations": [9]}, "citation 9 does not belong"),
        ({"action": "NEEDS_INFO", "amount": None, "citations": [9]}, "citation 9 does not belong"),
        ({"amount": Decimal("NaN")}, "finite number"),
        ({"amount": Decimal("sNaN")}, "finite number"),
        ({"amount": Decimal("Infinity")}, "finite number"),
    ],
)
def test_each_violation(overrides: dict, message: str) -> None:
    report = check(**overrides)
    assert not report.ok
    assert any(message in violation for violation in report.violations)


@pytest.mark.parametrize("action", ["ACCEPT_CLAIM", "MAKE_OFFER", "PROVIDE_EVIDENCE"])
def test_dispute_action_permissions(action: str) -> None:
    case = Case(source="dispute", amount=Decimal("100"), allowed_actions=[])
    report = check(action=action, case=case)
    assert any("does not currently allow" in violation for violation in report.violations)
    case.source = "complaint"
    case.allowed_actions = [action]
    report = check(action=action, case=case)
    assert any("only allowed for disputes" in violation for violation in report.violations)


@pytest.mark.parametrize("action", ["ACCEPT_CLAIM", "MAKE_OFFER", "REFUND"])
def test_every_money_action_requires_amount_and_citations(action: str) -> None:
    report = check(action=action, amount=None, citations=[])
    assert "A money action requires an amount" in report.violations
    assert "At least one evidence citation is required" in report.violations


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_deadline_boundary(offset: int) -> None:
    case = Case(
        source="dispute", amount=Decimal("100"), allowed_actions=["MAKE_OFFER"],
        response_due_at=NOW + timedelta(seconds=offset),
    )
    report = check(case=case)
    assert ("The response deadline has passed" in report.violations) == (offset < 0)


def test_needs_info_still_checks_deadline() -> None:
    report = check(
        action="NEEDS_INFO", amount=None, citations=[],
        case=Case(amount=Decimal("100"), response_due_at=NOW - timedelta(seconds=1)),
    )
    assert report.violations == ["The response deadline has passed"]


@pytest.mark.parametrize(("amount", "warns"), [("30", False), ("30.01", True)])
def test_offer_warning_boundary(amount: str, warns: bool) -> None:
    report = check(amount=Decimal(amount))
    assert report.ok
    assert bool(report.warnings) == warns


@pytest.mark.parametrize("action", ["ACCEPT_CLAIM", "MAKE_OFFER", "REFUND"])
@pytest.mark.parametrize("citations", [[], [1], [1, 1]])
def test_money_actions_warn_for_fewer_than_two_distinct_citations(action: str, citations: list[int]) -> None:
    report = check(action=action, citations=citations)
    assert any("two distinct" in warning for warning in report.warnings)


def test_nonmoney_actions_have_no_evidence_count_warning() -> None:
    assert not check(action="PROVIDE_EVIDENCE", amount=None, citations=[1]).warnings


def test_all_failures_are_collected_and_inputs_are_unchanged() -> None:
    case = Case(
        source="complaint", amount=Decimal("100"), currency="USD", allowed_actions=[],
        response_due_at=NOW - timedelta(days=1),
    )
    citations = [8, 9]
    report = check(
        action="ACCEPT_CLAIM", amount=Decimal("101.001"), currency="EUR",
        case=case, citations=citations, refundable=Decimal("10"),
    )
    assert len(report.violations) == 10
    assert citations == [8, 9]
    assert case.allowed_actions == []
    assert case.amount == Decimal("100")
    result = report.as_dict()
    result["violations"].clear()
    assert report.violations


def test_no_refundable_limit_and_exact_limits() -> None:
    assert check(action="ACCEPT_CLAIM", amount=Decimal("100.00"), refundable=None).ok
    assert check(action="ACCEPT_CLAIM", amount=Decimal("100.00"), refundable=Decimal("100")).ok
