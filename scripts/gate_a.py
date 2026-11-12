"""Gate A: do the PayPal sandbox keys work, and can this app read disputes?

    uv run python scripts/gate_a.py

Prints variable names and results only, never a client id, secret or token.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # lets `paypal_client` import when run as a script
sys.stdout.reconfigure(encoding="utf-8")

import httpx  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from paypal_client import PayPalClient, PayPalError  # noqa: E402
from paypal_client.client import BASE_URL  # noqa: E402

DISPUTE_SCOPES = ("disputes/read-seller", "disputes/update-seller")


def check_token(client_id: str, client_secret: str) -> bool:
    response = httpx.post(
        BASE_URL + "/v1/oauth2/token",
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        headers={"Accept": "application/json"},
        timeout=30,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code != 200:
        print(f"token: HTTP {response.status_code} {payload.get('error', '')} {payload.get('error_description', '')}")
        print("The keys were rejected. Check that they come from the Sandbox tab, not Live.")
        return False
    scopes = str(payload.get("scope", "")).split()
    print(f"token: ok, {len(scopes)} scopes, app id {payload.get('app_id', '?')}")
    for needed in DISPUTE_SCOPES:
        present = any(scope.endswith(needed) for scope in scopes)
        print(f"  scope {needed}: {'yes' if present else 'NO'}")
    if not any(scope.endswith(DISPUTE_SCOPES[0]) for scope in scopes):
        print("The app has no dispute scope. Turn on the Customer disputes add-on for this REST app.")
    return True


def check_disputes() -> bool:
    client = PayPalClient.from_env()
    try:
        items = client.list_disputes()
    except PayPalError as error:
        print(f"disputes: HTTP {error.status} {error.name}: {error.message} (debug_id={error.debug_id})")
        if error.status == 403:
            print("Disputes are forbidden for this app. Fall back to the complaint refund path (plan section 8).")
        return False
    finally:
        client.close()
    print(f"disputes: ok, {len(items)} listed")
    for item in items:
        amount = item.get("dispute_amount") or {}
        print(
            f"  {item.get('dispute_id')}  {item.get('status')}  {item.get('dispute_state')}  "
            f"{item.get('reason')}  {amount.get('value')} {amount.get('currency_code')}  "
            f"due {item.get('seller_response_due_date', '-')}"
        )
    return True


def main() -> int:
    load_dotenv(ROOT / ".env")
    missing = [name for name in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET") if not os.environ.get(name)]
    if missing:
        print("Missing or empty in .env:", ", ".join(missing))
        return 2
    print("PAYPAL_ENV =", os.environ.get("PAYPAL_ENV", "sandbox"))
    token_ok = check_token(os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"])
    disputes_ok = token_ok and check_disputes()
    print("GATE A:", "PASS" if disputes_ok else "FAIL")
    return 0 if disputes_ok else 1


if __name__ == "__main__":
    sys.exit(main())
