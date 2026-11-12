"""Offline demo cases built from demo_data/cases.json. They never touch PayPal."""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from cases.models import Case
from demo_sites.data import WORLD

OFFLINE_PREFIX = "DEMO-"
_ALLOWED_OPTIONS = {
    "accept_claim": {"accept_claim_types": ["REFUND"]},
    "make_offer": {"offer_types": ["REFUND"]},
    "provide_evidence": {"evidence_types": ["PROOF_OF_FULFILLMENT", "OTHER"]},
}


def is_offline(case: Case) -> bool:
    return case.paypal_dispute_id.startswith(OFFLINE_PREFIX)


def _snapshot(entry: dict, due_at: str) -> dict:
    key = entry["key"]
    amount = {"currency_code": entry["currency"], "value": entry["total"]}
    ship_to = entry["ship_to"]
    capture = {
        "id": f"{OFFLINE_PREFIX}CAPTURE-{key}",
        "status": "COMPLETED",
        "amount": amount,
        "supplementary_data": {"related_ids": {"order_id": f"{OFFLINE_PREFIX}ORDER-{key}"}},
    }
    return {
        "dispute": {
            "dispute_id": f"{OFFLINE_PREFIX}{key}",
            "status": "WAITING_FOR_SELLER_RESPONSE",
            "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
            "dispute_life_cycle_stage": "INQUIRY",
            "dispute_amount": amount,
            "seller_response_due_date": due_at,
            "disputed_transactions": [
                {
                    "seller_transaction_id": capture["id"],
                    "invoice_number": entry["order_ref"],
                    "custom": entry["order_ref"],
                    "gross_amount": amount,
                    "buyer": {"name": entry["customer"]["name"], "email": entry["customer"]["email"]},
                }
            ],
            "allowed_response_options": _ALLOWED_OPTIONS,
            "messages": [{"posted_by": "BUYER", "content": "I did not receive my item."}],
        },
        "capture": capture,
        "order": {
            "id": capture["supplementary_data"]["related_ids"]["order_id"],
            "purchase_units": [
                {
                    "invoice_id": entry["order_ref"],
                    "custom_id": entry["order_ref"],
                    "shipping": {
                        "name": {"full_name": ship_to["name"]},
                        "address": {
                            "address_line_1": ship_to["line1"],
                            "address_line_2": ship_to.get("line2", ""),
                            "admin_area_2": ship_to["city"],
                            "admin_area_1": ship_to["state"],
                            "postal_code": ship_to["postal_code"],
                            "country_code": ship_to["country_code"],
                        },
                    },
                    "payments": {"captures": [capture], "refunds": []},
                }
            ],
            "payer": {"email_address": entry["customer"]["email"]},
        },
    }


def seed_offline_cases(*, reset: bool = False) -> list[Case]:
    """Create (or with reset, rebuild) one offline case per demo case."""
    due = timezone.now() + timedelta(days=10)
    cases = []
    for entry in WORLD["cases"]:
        dispute_id = f"{OFFLINE_PREFIX}{entry['key']}"
        existing = Case.objects.filter(paypal_dispute_id=dispute_id).first()
        if existing and not reset:
            cases.append(existing)
            continue
        if existing:
            existing.delete()
        snapshot = _snapshot(entry, due.isoformat())
        cases.append(
            Case.objects.create(
                source=Case.Source.DISPUTE,
                state=Case.State.NEW,
                paypal_dispute_id=dispute_id,
                paypal_capture_id=snapshot["capture"]["id"],
                paypal_order_id=snapshot["order"]["id"],
                order_ref=entry["order_ref"],
                buyer_name=entry["customer"]["name"],
                buyer_email=entry["customer"]["email"],
                amount=Decimal(entry["total"]),
                currency=entry["currency"],
                reason="MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
                stage="INQUIRY",
                dispute_status="WAITING_FOR_SELLER_RESPONSE",
                response_due_at=due,
                allowed_actions=["ACCEPT_CLAIM", "MAKE_OFFER", "PROVIDE_EVIDENCE"],
                paypal_snapshot=snapshot,
            )
        )
    return cases
