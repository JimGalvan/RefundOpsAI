"""Run against an already running demo server: python scripts/demo_sites_smoke.py 8101."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", type=int)
    args = parser.parse_args()
    world = json.loads(
        (Path(__file__).resolve().parent.parent / "demo_data" / "cases.json").read_text(
            encoding="utf-8"
        )
    )
    credentials = world["sites"]["portal"]["login"]
    case = next(case for case in world["cases"] if case["order_ref"] == "ORD-10482")
    step = "Launch Chromium"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            step = "Portal login"
            page.goto(f"http://{world['sites']['portal']['host']}:{args.port}/")
            page.get_by_label("Email", exact=True).fill(credentials["username"])
            page.get_by_label("Password", exact=True).fill(credentials["password"])
            page.get_by_role("button", name="Sign in", exact=True).click()
            expect(
                page.get_by_role("heading", name="Orders", exact=True)
            ).to_be_visible()
            print(f"PASS: {step}")
            step = "Search ORD-10482"
            page.get_by_label("Order number", exact=True).fill(case["order_ref"])
            page.get_by_role("button", name="Search", exact=True).click()
            expect(
                page.get_by_role("link", name="View order", exact=False)
            ).to_have_count(1)
            page.get_by_role("link", name="View order", exact=False).click()
            expect(
                page.get_by_role("heading", name=case["order_ref"], exact=True)
            ).to_be_visible()
            print(f"PASS: {step}")
            step = "Follow carrier tracking link"
            page.get_by_role("link", name="Track on carrier site", exact=True).click()
            expect(page).to_have_url(
                f"http://{world['sites']['tracking']['host']}:{args.port}/track/{case['fulfillment']['tracking_number']}/"
            )
            print(f"PASS: {step}")
            step = "Read shipment summary"
            expect(
                page.get_by_role(
                    "heading", name=case["tracking"]["summary"], exact=True
                )
            ).to_be_visible()
            print(f"PASS: {step} - {case['tracking']['summary']}")
            browser.close()
    except Exception as error:
        print(f"FAIL: {step} - {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
