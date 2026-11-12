"""Refresh the PayPal facts on cases without changing merchant workflow state."""

from dataclasses import dataclass, field
from datetime import UTC
from decimal import Decimal

import httpx
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_naive

from paypal_client import PayPalClient, PayPalError

from .models import Case


@dataclass
class SyncResult:
    created: list[int] = field(default_factory=list)
    updated: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def allowed_actions(dispute: dict) -> list[str]:
    actions = ("ACCEPT_CLAIM", "MAKE_OFFER", "PROVIDE_EVIDENCE")
    options = dispute.get("allowed_response_options")
    if isinstance(options, dict):
        return [action for action in actions if action.lower() in options]
    rels = {link.get("rel") for link in dispute.get("links") or []}
    return [action for action in actions if action.lower().replace("_", "-") in rels]


def parse_dispute(dispute: dict, capture: dict | None, order: dict | None) -> dict:
    transactions = dispute.get("disputed_transactions") or [{}]
    transaction = transactions[0] or {}
    unit = ((order or {}).get("purchase_units") or [{}])[0] or {}
    buyer = transaction.get("buyer") or {}
    payer = (order or {}).get("payer") or {}
    payer_name = payer.get("name") or {}
    amount = dispute.get("dispute_amount") or transaction.get("gross_amount") or {}
    due = dispute.get("seller_response_due_date")
    try:
        due_at = parse_datetime(due) if due else None
    except ValueError:
        due_at = None
    if due_at is not None and is_naive(due_at):
        due_at = due_at.replace(tzinfo=UTC)
    return {
        "paypal_dispute_id": dispute.get("dispute_id") or "",
        "paypal_capture_id": transaction.get("seller_transaction_id") or "",
        "paypal_order_id": PayPalClient.extract_order_id(capture or {}) or "",
        "order_ref": unit.get("invoice_id") or transaction.get("invoice_number")
        or transaction.get("custom") or "",
        "buyer_name": buyer.get("name") or " ".join(
            part for part in (payer_name.get("given_name"), payer_name.get("surname"))
            if part
        ),
        "buyer_email": buyer.get("email") or payer.get("email_address") or "",
        "amount": Decimal(amount.get("value") or "0"),
        "currency": amount.get("currency_code") or "",
        "reason": dispute.get("reason") or "",
        "stage": dispute.get("dispute_life_cycle_stage") or "",
        "dispute_status": dispute.get("status") or "",
        "response_due_at": due_at,
        "allowed_actions": allowed_actions(dispute),
        "paypal_snapshot": {"dispute": dispute, "capture": capture, "order": order},
    }


def sync_one(client: PayPalClient, dispute_id: str) -> tuple[Case, bool]:
    dispute = client.get_dispute(dispute_id)
    capture = order = None
    errors: list[str] = []
    transaction = (dispute.get("disputed_transactions") or [{}])[0] or {}
    capture_id = transaction.get("seller_transaction_id")
    if capture_id:
        try:
            capture = client.get_capture(capture_id)
        except (PayPalError, httpx.HTTPError) as exc:
            errors.append(f"capture {capture_id}: {exc}")
    order_id = client.extract_order_id(capture or {})
    if order_id:
        try:
            order = client.get_order(order_id)
        except (PayPalError, httpx.HTTPError) as exc:
            errors.append(f"order {order_id}: {exc}")
    values = parse_dispute(dispute, capture, order)
    # The requested ID remains authoritative if a sparse response omits it.
    values.pop("paypal_dispute_id")
    if errors:
        values["paypal_snapshot"]["snapshot_errors"] = errors
    return Case.objects.update_or_create(
        paypal_dispute_id=dispute_id,
        defaults=values,
        create_defaults={**values, "source": Case.Source.DISPUTE, "state": Case.State.NEW},
    )


def sync_disputes(
    client: PayPalClient, *, dispute_state: str | None = None
) -> SyncResult:
    result = SyncResult()
    for item in client.list_disputes(dispute_state=dispute_state):
        dispute_id = item.get("dispute_id")
        if not dispute_id:
            result.errors.append("Missing dispute_id in PayPal dispute list item")
            continue
        try:
            case, created = sync_one(client, dispute_id)
        except PayPalError as exc:
            result.errors.append(f"{dispute_id}: {exc}")
        else:
            target = result.created if created else result.updated
            target.append(case.pk)
    return result
