"""Create sandbox demo payments, then capture them after browser approval."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import argparse
import json
from typing import Any

import httpx

from paypal_client import PayPalClient, PayPalError
from paypal_client.client import build_order_payload

CASES_PATH = ROOT / "demo_data" / "cases.json"
STATE_PATH = ROOT / "data" / "seed_state.json"
JSON = dict[str, Any]


def _case_keys(value: str) -> list[str]:
    keys = list(dict.fromkeys(key.strip().upper() for key in value.split(",")))
    if not keys or any(key not in {"A", "B"} for key in keys):
        raise argparse.ArgumentTypeError("cases must be A, B, or A,B")
    return keys


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser(
        "create", help="Create orders and print buyer approval URLs"
    )
    create.add_argument("--cases", type=_case_keys, default=["A", "B"])
    create.add_argument("--suffix", default="")
    create.add_argument("--dry-run", action="store_true")
    capture = commands.add_parser("capture", help="Capture approved orders")
    capture.add_argument("--cases", type=_case_keys, default=["A", "B"])
    commands.add_parser("status", help="Refresh and print seeded order statuses")
    return parser


def _read_state() -> JSON:
    if not STATE_PATH.exists():
        return {"cases": {}}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def _write_state(state: JSON) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    temporary.replace(STATE_PATH)


def _order_args(case: JSON, invoice_id: str) -> JSON:
    return {
        "invoice_id": invoice_id,
        "amount": case["total"],
        "currency": case.get("currency", "USD"),
        "description": case["description"],
        "shipping": case["ship_to"],
    }


def _has_issue(error: PayPalError, issue: str) -> bool:
    return error.name == issue or any(
        detail.get("issue") == issue for detail in error.details
    )


def _create(client: PayPalClient, cases: list[JSON], suffix: str) -> None:
    state = _read_state()
    print("CASE  INVOICE ID  ORDER ID  APPROVAL URL")
    for case in cases:
        base_invoice = case["order_ref"] + suffix
        for attempt in range(1, 4):
            invoice_id = base_invoice + (f"-r{attempt}" if attempt > 1 else "")
            try:
                order = client.create_order(**_order_args(case, invoice_id))
                break
            except PayPalError as exc:
                if not _has_issue(exc, "DUPLICATE_INVOICE_ID") or attempt == 3:
                    raise
        approval_url = client.approval_url(order)
        state["cases"][case["key"]] = {
            "invoice_id": invoice_id,
            "order_id": order["id"],
            "approval_url": approval_url,
            "status": order.get("status", "CREATED"),
        }
        _write_state(state)
        print(f"{case['key']}  {invoice_id}  {order['id']}  {approval_url}")


def _first_capture(order: JSON) -> JSON | None:
    for unit in order.get("purchase_units", []):
        captures = unit.get("payments", {}).get("captures", [])
        if captures:
            return captures[0]
    return None


def _capture(client: PayPalClient, state: JSON, keys: list[str]) -> None:
    for key in keys:
        entry = state["cases"].get(key)
        if entry is None:
            print(f"{key}: no seeded order; run create first")
            continue
        if entry.get("capture_id"):
            print(f"{key}: already captured; capture id {entry['capture_id']}")
            continue
        try:
            order = client.capture_order(entry["order_id"])
        except PayPalError as exc:
            if (
                _has_issue(exc, "ORDER_NOT_APPROVED")
                or exc.name == "UNPROCESSABLE_ENTITY"
            ):
                print(
                    f"{key}: not approved yet; open {entry.get('approval_url', 'the approval URL')}"
                )
                continue
            raise
        capture = _first_capture(order)
        if capture is None:
            raise ValueError(
                f"{key}: capture response has no capture id; check order status"
            )
        entry.update(
            capture_id=capture["id"],
            status=order.get("status"),
            capture_status=capture.get("status"),
            amount=capture.get("amount"),
        )
        _write_state(state)
        amount = capture.get("amount", {})
        print(
            f"{key}: {capture['id']}  {capture.get('status', '-')}  "
            f"{amount.get('value', '-')} {amount.get('currency_code', '')}"
        )


def _status(client: PayPalClient, state: JSON) -> None:
    for key, entry in state["cases"].items():
        order = client.get_order(entry["order_id"])
        capture = _first_capture(order)
        entry["status"] = order.get("status", "UNKNOWN")
        if capture:
            entry["capture_id"] = capture["id"]
        _write_state(state)
        print(
            f"{key}: {entry['order_id']}  {entry['status']}  capture={entry.get('capture_id', '-')}"
        )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "create":
            world = json.loads(CASES_PATH.read_text(encoding="utf-8"))
            cases = [case for case in world["cases"] if case["key"] in args.cases]
            if args.dry_run:
                for case in cases:
                    payload = build_order_payload(
                        **_order_args(case, case["order_ref"] + args.suffix)
                    )
                    print(f"Case {case['key']}:")
                    print(json.dumps(payload, indent=2))
                return 0
        else:
            state = _read_state()
            if not state["cases"]:
                print("No seeded orders; run create first.")
                return 0

        # Dry runs never load credentials, even when a local dotenv file exists.
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
        with PayPalClient.from_env() as client:
            if args.command == "create":
                _create(client, cases, args.suffix)
            elif args.command == "capture":
                _capture(client, state, args.cases)
            else:
                _status(client, state)
    except (PayPalError, ValueError, OSError, httpx.HTTPError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
