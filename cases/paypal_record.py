"""Stable, quotable text containing the facts fetched from PayPal."""

from datetime import UTC
from decimal import Decimal

from .models import Case


def _unit(order: dict | None) -> dict:
    return ((order or {}).get("purchase_units") or [{}])[0] or {}


def _refunds(order: dict | None) -> list[dict]:
    return (_unit(order).get("payments") or {}).get("refunds") or []


def held_for_complaint(order: dict | None) -> bool:
    """True while PayPal holds the capture for the open buyer complaint."""
    captures = (_unit(order).get("payments") or {}).get("captures") or []
    return any((capture.get("status_details") or {}).get("reason") == "BUYER_COMPLAINT" for capture in captures)


def counted_refunds(order: dict | None) -> list[dict]:
    """Refunds that have gone to the buyer or are on their way.

    While the capture is held for the buyer complaint, the sandbox lists the seller's net amount as a PENDING refund.
    That entry is the hold, not money returned to the buyer, so it must not shrink what can still be refunded.
    """
    held = held_for_complaint(order)
    return [r for r in _refunds(order) if r.get("status") == "COMPLETED" or (r.get("status") == "PENDING" and not held)]


def refund_total(order: dict | None) -> Decimal:
    return sum((Decimal((refund.get("amount") or {}).get("value") or "0") for refund in counted_refunds(order)), Decimal("0"))


def _money(amount: dict, fallback_currency: str = "") -> str:
    value = amount.get("value")
    if value is None or value == "":
        return ""
    currency = amount.get("currency_code") or fallback_currency
    return f"{Decimal(value):.2f} {currency}".strip()


def build_paypal_record(case: Case) -> str:
    snapshot = case.paypal_snapshot or {}
    dispute = snapshot.get("dispute") or {}
    capture = snapshot.get("capture")
    order = snapshot.get("order")
    unit = _unit(order)
    lines = ["PAYPAL RECORD (read-only, fetched from PayPal)"]

    def add(label: str, value: str | None) -> None:
        if value:
            lines.append(f"{label}: {value}")

    add("Dispute ID", case.paypal_dispute_id)
    add("Reason", case.reason)
    add("Stage", case.stage)
    add("Status", case.dispute_status)
    transaction = (dispute.get("disputed_transactions") or [{}])[0] or {}
    amount = dispute.get("dispute_amount") or transaction.get("gross_amount") or {}
    add("Dispute amount", _money(amount))
    if case.response_due_at is not None:
        add("Respond by", case.response_due_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC"))
    add("Allowed responses", ", ".join(case.allowed_actions or []))
    buyer = case.buyer_name or ""
    if case.buyer_email:
        buyer = f"{buyer} ({case.buyer_email})" if buyer else case.buyer_email
    add("Buyer", buyer)
    for message in dispute.get("messages") or []:
        if message.get("posted_by") == "BUYER":
            content = message.get("content") or ""
            add("Buyer message", " ".join(content[:400].split()))
            break
    if capture is not None:
        payment = " ".join(filter(None, (
            f"capture {capture['id']}" if capture.get("id") else "",
            _money(capture.get("amount") or {}),
        )))
        if capture.get("status"):
            payment += f", status {capture['status']}" if payment else f"status {capture['status']}"
        add("Payment", payment)
    if order is not None:
        refunds = []
        counted = counted_refunds(order)
        for refund in counted:
            money = _money(refund.get("amount") or {}, case.currency)
            details = ", ".join(filter(None, (
                f"id {refund['id']}" if refund.get("id") else "",
                f"status {refund['status']}" if refund.get("status") else "",
            )))
            text = f"{money} ({details})" if money and details else money or details
            if text:
                refunds.append(text)
        if refunds:
            add("Earlier refunds", "; ".join(refunds))
        else:
            add("Earlier refunds", "none")
        if held_for_complaint(order) and len(counted) < len(_refunds(order)):
            add("Complaint hold", "PayPal lists a pending entry for the held funds while the capture is held for a buyer complaint; no refund has been paid to the buyer")
    add("Store order number (invoice_id)", unit.get("invoice_id"))
    shipping = unit.get("shipping") or {}
    address = shipping.get("address") or {}
    parts = [(shipping.get("name") or {}).get("full_name")]
    parts.extend(address.get(key) for key in (
        "address_line_1", "address_line_2", "admin_area_2", "admin_area_1",
        "postal_code", "country_code",
    ))
    add("Ship-to", ", ".join(part for part in parts if part))
    return "\n".join(lines)
