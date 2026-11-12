import json
from decimal import Decimal
from pathlib import Path

import pytest

from cases.models import Case
from cases.paypal_record import build_paypal_record, refund_total
from cases.sync import parse_dispute

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def test_record_120_exact() -> None:
    case = Case(**parse_dispute(**load_fixture("intake_120")))
    expected = """PAYPAL RECORD (read-only, fetched from PayPal)
Dispute ID: PP-D-27803
Reason: MERCHANDISE_OR_SERVICE_NOT_RECEIVED
Stage: INQUIRY
Status: WAITING_FOR_SELLER_RESPONSE
Dispute amount: 120.00 USD
Respond by: 2026-11-26 07:59 UTC
Allowed responses: ACCEPT_CLAIM, MAKE_OFFER, PROVIDE_EVIDENCE
Buyer: Sarah Okafor (sarah@example.com)
Buyer message: My order has not arrived. Please help.
Payment: capture CAP-120 120.00 USD, status COMPLETED
Earlier refunds: none
Store order number (invoice_id): ORD-10482
Ship-to: Sarah Okafor, 418 Alder Street, Apt 3B, Portland, OR, 97205, US"""
    assert build_paypal_record(case) == expected
    assert build_paypal_record(case) == expected


def test_record_90_exact() -> None:
    case = Case(**parse_dispute(**load_fixture("intake_90")))
    assert build_paypal_record(case) == """PAYPAL RECORD (read-only, fetched from PayPal)
Dispute ID: PP-D-27804
Reason: MERCHANDISE_OR_SERVICE_NOT_RECEIVED
Stage: INQUIRY
Status: WAITING_FOR_SELLER_RESPONSE
Dispute amount: 90.00 USD
Respond by: 2026-11-27 07:30 UTC
Allowed responses: ACCEPT_CLAIM, MAKE_OFFER, PROVIDE_EVIDENCE
Buyer: Alex Chen (alex@example.com)
Buyer message: Only part of my order arrived.
Payment: capture CAP-90 90.00 USD, status PARTIALLY_REFUNDED
Earlier refunds: 25.00 USD (id REF-25, status COMPLETED)
Store order number (invoice_id): ORD-10483
Ship-to: Alex Chen, 52 Pine Road, Seattle, WA, 98101, US"""


def test_missing_record_omits_unknowns() -> None:
    case = Case(**parse_dispute(**load_fixture("intake_missing")))
    assert build_paypal_record(case) == "PAYPAL RECORD (read-only, fetched from PayPal)\nDispute ID: PP-D-MISSING"
    assert build_paypal_record(Case(amount=Decimal("0"))) == "PAYPAL RECORD (read-only, fetched from PayPal)"


def test_message_limit_and_single_line() -> None:
    fixture = load_fixture("intake_120")
    fixture["dispute"]["messages"] = [{"posted_by": "BUYER", "content": "x" * 401}]
    record = build_paypal_record(Case(**parse_dispute(**fixture)))
    assert next(line for line in record.splitlines() if line.startswith("Buyer message:")) == "Buyer message: " + "x" * 400
    fixture["dispute"]["messages"][0]["content"] = "First line\r\nSecond line"
    assert "Buyer message: First line Second line\n" in build_paypal_record(Case(**parse_dispute(**fixture)))


@pytest.mark.parametrize("order", [None, {}, {"purchase_units": []}, {"purchase_units": [{}]}])
def test_missing_refund_total(order: dict | None) -> None:
    assert refund_total(order) == Decimal("0")


def test_refund_total_statuses_and_first_unit() -> None:
    order = load_fixture("intake_90")["order"]
    refunds = order["purchase_units"][0]["payments"]["refunds"]
    refunds.extend([
        {"id": "PENDING-10", "status": "PENDING", "amount": {"value": "10.50"}},
        {"status": "FAILED", "amount": {"value": "100.00"}},
        {"status": "CANCELLED", "amount": {"value": "100.00"}},
        {"amount": {"value": "100.00"}},
        {"status": "COMPLETED"},
    ])
    order["purchase_units"].append({"payments": {"refunds": [{"status": "COMPLETED", "amount": {"value": "999.00"}}]}})
    assert refund_total(order) == Decimal("35.50")


def test_refund_currency_fallback_and_multiple_refunds() -> None:
    fixture = load_fixture("intake_90")
    refunds = fixture["order"]["purchase_units"][0]["payments"]["refunds"]
    del refunds[0]["amount"]["currency_code"]
    refunds.append({"id": "REF-PENDING", "status": "PENDING", "amount": {"value": "5.00"}})
    record = build_paypal_record(Case(**parse_dispute(**fixture)))
    assert "Earlier refunds: 25.00 USD (id REF-25, status COMPLETED); 5.00 USD (id REF-PENDING, status PENDING)\n" in record
