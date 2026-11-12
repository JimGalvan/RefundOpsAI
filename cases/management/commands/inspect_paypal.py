"""Inspect sandbox payment and refund reads without deciding or executing a case."""

import json
from pathlib import Path

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from cases.models import Case
from cases.paypal_record import _refunds, refund_total
from console.demo import is_offline
from paypal_client import PayPalClient, PayPalError


class Command(BaseCommand):
    help = "Read sandbox disputes, captures, orders and individual refunds; never approve or execute an action."

    def add_arguments(self, parser) -> None:
        parser.add_argument("case_ids", nargs="+", type=int)
        parser.add_argument("--cached", action="store_true", help="Inspect stored snapshots without any API calls.")
        parser.add_argument("--save", action="store_true", help="Save response bodies under data/paypal_checks/ (gitignored).")

    def handle(self, *args, **options) -> None:
        ids = list(dict.fromkeys(options["case_ids"]))
        cases = Case.objects.in_bulk(ids)
        missing = [pk for pk in ids if pk not in cases]
        if missing:
            raise CommandError(f"Cases not found: {missing}")
        offline = [pk for pk in ids if is_offline(cases[pk])]
        if offline and not options["cached"]:
            raise CommandError(f"Offline demo cases cannot be inspected against PayPal: {offline}")

        client = None if options["cached"] else PayPalClient.from_env()
        report = {
            "read_at": timezone.now().isoformat(),
            "source": "cached snapshots" if options["cached"] else "live sandbox GET requests",
            "cases": [],
        }
        try:
            for pk in ids:
                case = cases[pk]
                reads = self._inspect(case, client)
                report["cases"].append({"case_id": pk, "summary": _summary(case, reads), "reads": reads})
        finally:
            if client is not None:
                client.close()

        self.stdout.write(json.dumps({
            "read_at": report["read_at"], "source": report["source"],
            "cases": [{"case_id": item["case_id"], **item["summary"]} for item in report["cases"]],
        }, indent=2))
        if options["save"]:
            folder = Path(settings.BASE_DIR) / "data" / "paypal_checks"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{'-'.join(map(str, ids))}-{timezone.now():%Y%m%dT%H%M%S%fZ}.json"
            path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            self.stdout.write(f"Response bodies saved to {path.relative_to(settings.BASE_DIR)}")
        self.stdout.write("No case state, decision, recommendation or PayPal balance was changed.")
        if any(item["reads"]["errors"] for item in report["cases"]):
            raise CommandError("Some PayPal reads failed; inspect the errors above.")

    @staticmethod
    def _inspect(case: Case, client: PayPalClient | None) -> dict:
        if client is None:
            return {**(case.paypal_snapshot or {}), "refund_details": {}, "errors": {}}
        reads = {"dispute": None, "capture": None, "order": None, "refund_details": {}, "errors": {}}

        def read(label, method, identifier):
            if not identifier:
                reads["errors"][label] = "No resource ID is available."
                return None
            try:
                return method(identifier)
            except (PayPalError, httpx.HTTPError) as error:
                reads["errors"][label] = str(error)
                return None

        reads["dispute"] = read("dispute", client.get_dispute, case.paypal_dispute_id)
        reads["capture"] = read("capture", client.get_capture, case.paypal_capture_id)
        order_id = case.paypal_order_id or client.extract_order_id(reads["capture"] or {})
        reads["order"] = read("order", client.get_order, order_id)
        for refund in _refunds(reads["order"]):
            refund_id = refund.get("id")
            if refund_id:
                reads["refund_details"][refund_id] = read(f"refund:{refund_id}", client.get_refund, refund_id)
        return reads


def _summary(case: Case, reads: dict) -> dict:
    dispute = reads.get("dispute") or {}
    capture = reads.get("capture") or {}
    refunds = []
    for refund in _refunds(reads.get("order")):
        detail = (reads.get("refund_details") or {}).get(refund.get("id"))
        refunds.append({
            "id": refund.get("id"), "order_status": refund.get("status"), "amount": refund.get("amount"),
            "total_refunded_amount": (refund.get("seller_payable_breakdown") or {}).get("total_refunded_amount"),
            "direct_lookup": {key: detail.get(key) for key in ("id", "status", "status_details", "amount", "seller_payable_breakdown")}
            if detail is not None else None,
        })
    warnings = []
    reason = (capture.get("status_details") or {}).get("reason")
    if reason == "BUYER_COMPLAINT":
        warnings.append("Capture is held for a buyer complaint. The capture status alone does not prove a buyer refund.")
    if any(refund["order_status"] == "PENDING" for refund in refunds):
        warnings.append("Pending refund entries reduce the current refundable calculation; reconcile their direct reads before approval.")
    return {
        "order_ref": case.order_ref,
        "workflow_state": case.state,
        "dispute_id": case.paypal_dispute_id,
        "dispute_status": dispute.get("status"),
        "allowed_response_options": dispute.get("allowed_response_options"),
        "capture_id": capture.get("id"),
        "capture_status": capture.get("status"),
        "capture_pending_reason": reason,
        "refunds_counted_by_current_guardrails": str(refund_total(reads.get("order"))),
        "refunds": refunds,
        "warnings": warnings,
        "errors": reads["errors"],
    }
