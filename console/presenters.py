"""Turns case data into what the templates show. No HTML is built here except citation links."""

import re
from datetime import timedelta
from urllib.parse import urlsplit

from django.utils import timezone
from django.utils.html import escape
from django.utils.safestring import mark_safe

from cases.models import AgentEvent, Case, Evidence, Investigation
from cases.paypal_record import refund_total
from console.hub import hub
from demo_sites.data import WORLD

REASONS = {
    "MERCHANDISE_OR_SERVICE_NOT_RECEIVED": "Item not received",
    "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED": "Not as described",
    "UNAUTHORISED": "Unauthorized transaction",
    "UNAUTHORIZED": "Unauthorized transaction",
    "CREDIT_NOT_PROCESSED": "Credit not processed",
    "DUPLICATE_TRANSACTION": "Duplicate transaction",
    "INCORRECT_AMOUNT": "Incorrect amount",
}
STATE_LABELS = {
    "new": "Not investigated",
    "investigating": "Investigating",
    "ready": "Needs your decision",
    "approved": "Approved",
    "executing": "Sending to PayPal",
    "resolved": "Resolved",
    "rejected": "Rejected",
    "needs_info": "Needs more information",
    "failed": "Failed",
}
STATE_TONE = {
    "new": "muted", "investigating": "active", "ready": "attention", "approved": "active",
    "executing": "active", "resolved": "done", "rejected": "muted", "needs_info": "attention", "failed": "bad",
}
ACTION_LABELS = {
    "ACCEPT_CLAIM": "Accept the claim and refund",
    "MAKE_OFFER": "Make a settlement offer",
    "PROVIDE_EVIDENCE": "Contest with evidence",
    "REFUND": "Refund the payment",
    "NEEDS_INFO": "Needs more information",
}
SOURCE_ORDER = ["paypal", "portal", "carrier", "policy", "inbox", "other"]
SOURCE_TITLES = {
    "paypal": "PayPal",
    "portal": WORLD["sites"]["portal"]["brand"],
    "carrier": WORLD["sites"]["tracking"]["brand"],
    "policy": WORLD["sites"]["store"]["brand"],
    "inbox": WORLD["sites"]["inbox"]["brand"],
    "other": "Other",
}
_BRAND_TO_SOURCE = {
    "PayPal": "paypal",
    WORLD["sites"]["portal"]["brand"]: "portal",
    WORLD["sites"]["tracking"]["brand"]: "carrier",
    WORLD["sites"]["store"]["brand"]: "policy",
    WORLD["sites"]["inbox"]["brand"]: "inbox",
}
_CITATION = re.compile(r"\bE(\d+)\b")
INVESTIGABLE_STATES = {"new", "ready", "needs_info", "failed", "investigating"}


def approve_label(action: str, amount, currency: str) -> str:
    """The approve button names exactly what will be sent."""
    if action in {"ACCEPT_CLAIM", "REFUND"}:
        return f"Approve and refund {amount} {currency}"
    if action == "MAKE_OFFER":
        return f"Approve and offer {amount} {currency}"
    if action == "PROVIDE_EVIDENCE":
        return "Approve and send evidence"
    return "Approve"


def source_key(label: str) -> str:
    return _BRAND_TO_SOURCE.get(label, "other")


def human_reason(code: str) -> str:
    return REASONS.get(code, code.replace("_", " ").capitalize() if code else "")


def link_citations(text: str, known_ids: set[int]) -> str:
    """Escape the text and turn E21-style references to known evidence into anchor links."""

    def link(match: re.Match) -> str:
        number = int(match.group(1))
        if number not in known_ids:
            return match.group(0)
        return f'<a class="cite" href="#e{number}">E{number}</a>'

    return mark_safe(_CITATION.sub(link, escape(text)))


def due_info(case: Case) -> dict:
    due = case.response_due_at
    if due is None:
        return {"text": "", "date": "", "urgent": False}
    delta = due - timezone.now()
    if delta.total_seconds() < 0:
        text = "Past due"
    elif delta < timedelta(hours=1):
        text = "In under an hour"
    elif delta < timedelta(days=1):
        text = f"In {int(delta.total_seconds() // 3600)} hours"
    else:
        text = f"In {delta.days} days"
    return {"text": text, "date": f"{due:%b} {due.day}", "urgent": delta < timedelta(days=3)}


def case_row(case: Case) -> dict:
    return {
        "case": case,
        "reason": human_reason(case.reason),
        "state_label": STATE_LABELS.get(case.state, case.state),
        "state_tone": STATE_TONE.get(case.state, "muted"),
        "due": due_info(case),
        "offline": case.paypal_dispute_id.startswith("DEMO-"),
    }


def grid_row(row: dict) -> dict:
    """One queue row as plain data for the AG Grid view."""
    case = row["case"]
    due = row["due"]
    return {
        "id": case.pk,
        "href": f"/cases/{case.pk}/",
        "order_ref": case.order_ref,
        "dispute_id": case.paypal_dispute_id,
        "buyer": case.buyer_name,
        "offline": row["offline"],
        "reason": row["reason"],
        "amount": float(case.amount),
        "currency": case.currency,
        "due_sort": case.response_due_at.isoformat() if case.response_due_at else "9999",
        "due_date": due["date"],
        "due_text": due["text"],
        "due_urgent": due["urgent"],
        "state_label": row["state_label"],
        "state_tone": row["state_tone"],
    }


def ledger_rows(evidence: list[Evidence]) -> list[dict]:
    """The evidence as a flat table for the ledger view."""
    rows = []
    for group in group_evidence(evidence):
        for entry in group["entries"]:
            item = entry["item"]
            rows.append(
                {
                    "id": item.pk,
                    "key_label": f"E{item.pk}",
                    "key": entry["key"],
                    "source": item.source_label or "Other",
                    "kind": item.get_kind_display(),
                    "claim": item.claim,
                    "quote": item.quote,
                    "supports": item.supports,
                    "supports_label": entry["supports_label"],
                }
            )
    return rows


def record_boxes(case: Case) -> list[dict]:
    """The PayPal record as waybill boxes, built only from what PayPal returned."""
    snapshot = case.paypal_snapshot or {}
    order = snapshot.get("order") or {}
    unit = (order.get("purchase_units") or [{}])[0]
    shipping = unit.get("shipping") or {}
    address = shipping.get("address") or {}
    ship_to = ", ".join(
        part
        for part in [
            (shipping.get("name") or {}).get("full_name"),
            address.get("address_line_1"),
            address.get("address_line_2"),
            address.get("admin_area_2"),
            address.get("admin_area_1"),
            address.get("postal_code"),
            address.get("country_code"),
        ]
        if part
    )
    refunds = refund_total(order) if order else None
    due = due_info(case)
    boxes = [
        ("Dispute", case.paypal_dispute_id, True, False),
        ("Reason", human_reason(case.reason), False, False),
        ("Stage", case.stage.replace("_", " ").title(), False, False),
        ("Status", case.dispute_status.replace("_", " ").capitalize(), False, False),
        ("Amount", f"{case.amount} {case.currency}", True, False),
        ("Respond by", f"{due['date']} ({due['text'].lower()})" if due["date"] else "", False, False),
        ("Buyer", " ".join(filter(None, [case.buyer_name, f"({case.buyer_email})" if case.buyer_email else ""])), False, True),
        ("Payment", case.paypal_capture_id, True, True),
        ("Earlier refunds", "none" if refunds == 0 else (f"{refunds} {case.currency}" if refunds else ""), False, False),
        ("Store order", case.order_ref, True, False),
        ("Ship-to", ship_to, False, True),
    ]
    return [
        {"label": label, "value": value, "mono": mono, "wide": wide}
        for label, value, mono, wide in boxes
        if value
    ]


def group_evidence(evidence: list[Evidence]) -> list[dict]:
    groups: dict[str, list[dict]] = {key: [] for key in SOURCE_ORDER}
    for item in evidence:
        key = source_key(item.source_label)
        groups[key].append(
            {
                "item": item,
                "key": key,
                "supports_label": {
                    "buyer": "Supports the buyer",
                    "merchant": "Supports the merchant",
                    "neutral": "Neutral",
                    "untrusted": "Untrusted: instruction ignored",
                }.get(item.supports, item.supports),
                "host": urlsplit(item.source_url).netloc if item.source_url else "",
            }
        )
    return [
        {"key": key, "title": SOURCE_TITLES[key], "entries": entries}
        for key, entries in groups.items()
        if entries
    ]


def describe_event(event: AgentEvent) -> dict | None:
    """One readable line for the live log, or None for events the person does not need to see."""
    payload = event.payload or {}
    kind = event.type
    if kind == "browser.navigation.started":
        url = payload.get("url", "")
        return {"text": f"Opening {urlsplit(url).netloc}{urlsplit(url).path}", "class": ""}
    if kind == "browser.action.started":
        action = payload.get("action", "")
        target = payload.get("target", "")
        if action == "fill_credential":
            return {"text": "Signing in with the saved login (the agent never sees the password)", "class": ""}
        verb = {"click": "Clicking", "fill": "Entering text in", "type": "Typing in", "select": "Choosing in", "press": "Pressing"}.get(action, action)
        return {"text": f"{verb} {target}".strip(), "class": ""}
    if kind == "browser.action.failed":
        return {"text": f"Could not {payload.get('action', 'act')} on {payload.get('target', 'the page')}", "class": "blocked"}
    if kind == "sandbox.request_blocked":
        return {"text": f"Blocked a request to {payload.get('host', 'another site')} (outside the allowed sites)", "class": "blocked"}
    if kind == "agent.message":
        text = " ".join(str(payload.get("text", "")).split())
        return {"text": text[:220] + ("…" if len(text) > 220 else ""), "class": "agent"} if text else None
    if kind == "recommendation.started":
        return {"text": "Writing the recommendation from the verified facts", "class": ""}
    if kind == "run.failed":
        return {"text": f"The agent stopped: {payload.get('error', '')}"[:240], "class": "blocked"}
    return None


def audit_entries(case: Case) -> list[dict]:
    """Everything that happened on the case, oldest first."""
    entries: list[tuple] = []
    for inv in case.investigations.all():
        count = inv.evidence.count()
        entries.append((inv.started_at, "Investigation started", f"Model {inv.strong_model}; reading the merchant's tools."))
        if inv.finished_at:
            seconds = int((inv.finished_at - inv.started_at).total_seconds())
            detail = f"{inv.get_status_display()}: {count} verified facts in {seconds} seconds."
            if inv.error:
                detail += f" {inv.error}"
            entries.append((inv.finished_at, "Investigation finished", detail))
    for rec in case.recommendations.all():
        amount = f" {rec.amount} {rec.currency}" if rec.amount else ""
        entries.append((rec.created_at, "Recommendation", f"{ACTION_LABELS.get(rec.action, rec.action)}{amount}. {rec.headline}"))
    for decision in case.decisions.all():
        amount = f" {decision.amount} {case.currency}" if decision.amount else ""
        what = ACTION_LABELS.get(decision.action, decision.action) if decision.action else "Recommendation not used"
        entries.append((decision.decided_at, f"Merchant: {decision.get_outcome_display().lower()}", f"{what}{amount}. {decision.note}".strip()))
    for action in case.paypal_actions.all():
        ids = ", ".join(f"{key} {value}" for key, value in (action.paypal_ids or {}).items() if value)
        entries.append((action.created_at, f"PayPal call: {action.kind.replace('_', ' ')}", f"{action.get_status_display()}. {ids}"))
        if action.verified_at:
            summary = ", ".join(f"{key.replace('_', ' ')} {value}" for key, value in (action.verification or {}).items() if value)
            entries.append((action.verified_at, "Verified with PayPal", summary or "Read back from PayPal."))
    entries.sort(key=lambda entry: entry[0])
    return [
        {"when": f"{when:%b} {when.day}, {when:%H:%M:%S}", "what": what, "detail": detail}
        for when, what, detail in entries
    ]


def case_context(case: Case) -> dict:
    investigation = case.investigations.order_by("-pk").first()
    completed = case.investigations.filter(status=Investigation.Status.COMPLETED).order_by("-pk").first()
    evidence = list(case.evidence.filter(investigation=completed)) if completed else []
    recommendation = case.recommendations.order_by("-pk").first()
    decision = case.decisions.order_by("-pk").first()
    actions = list(case.paypal_actions.order_by("-pk"))
    known = {item.pk for item in evidence}
    running = case.pk in hub.running
    contradictions = []
    if recommendation:
        for entry in recommendation.contradictions or []:
            contradictions.append({"note": link_citations(entry.get("note", ""), known), "evidence": entry.get("evidence", [])})
    return {
        "case": case,
        "row": case_row(case),
        "boxes": record_boxes(case),
        "running": running,
        "investigation": investigation,
        "completed": completed,
        "groups": group_evidence(evidence),
        "ledger_rows": ledger_rows(evidence),
        "evidence_count": len(evidence),
        "recommendation": recommendation,
        "rec_label": ACTION_LABELS.get(recommendation.action, recommendation.action) if recommendation else "",
        "rationale": link_citations(recommendation.rationale, known) if recommendation else "",
        "contradictions": contradictions,
        "alternatives": (recommendation.alternatives if recommendation else []),
        "decision": decision,
        "last_action": actions[0] if actions else None,
        "audit": audit_entries(case),
        "allowed": [{"value": a, "label": ACTION_LABELS.get(a, a)} for a in case.allowed_actions],
        "offline": case.paypal_dispute_id.startswith("DEMO-"),
        "action_labels": ACTION_LABELS,
        "can_investigate": case.state in INVESTIGABLE_STATES and not running,
        "can_decide": bool(recommendation) and case.state == Case.State.READY,
        "approve_label": approve_label(recommendation.action, recommendation.amount, recommendation.currency) if recommendation else "",
    }
