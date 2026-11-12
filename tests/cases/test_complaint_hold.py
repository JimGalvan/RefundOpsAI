"""A sandbox complaint hold is listed as a PENDING refund but is not money returned to the buyer."""

from decimal import Decimal

from cases.paypal_record import counted_refunds, refund_total


def order(capture_details, refund_status):
    return {
        "purchase_units": [
            {
                "payments": {
                    "captures": [{"status": "PENDING", "status_details": capture_details}],
                    "refunds": [{"status": refund_status, "amount": {"currency_code": "USD", "value": "115.32"}}],
                }
            }
        ]
    }


def test_pending_entry_under_a_buyer_complaint_hold_is_not_a_refund():
    held = order({"reason": "BUYER_COMPLAINT"}, "PENDING")
    assert counted_refunds(held) == []
    assert refund_total(held) == Decimal("0")


def test_pending_refund_without_a_hold_still_counts():
    assert refund_total(order(None, "PENDING")) == Decimal("115.32")


def test_completed_refund_counts_even_during_a_hold():
    assert refund_total(order({"reason": "BUYER_COMPLAINT"}, "COMPLETED")) == Decimal("115.32")
