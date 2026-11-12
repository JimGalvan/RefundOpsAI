import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from asgiref.sync import iscoroutinefunction
from django.core.management import call_command
from django.http import HttpRequest, HttpResponse
from django.test import Client, RequestFactory
from django.utils.html import escape

from demo_sites.data import (
    WORLD,
    display_date,
    messages,
    seed_path,
    seed_time,
    timestamp,
)
from demo_sites.middleware import HostRoutingMiddleware


@pytest.fixture(autouse=True)
def seed_directory(settings: Any, tmp_path: Path) -> None:
    settings.DATA_DIR = tmp_path


def login(client: Client, site: str) -> None:
    credentials = WORLD["sites"][site]["login"]
    response = client.post(
        "/login/",
        {"email": credentials["username"], "password": credentials["password"]},
        HTTP_HOST=WORLD["sites"][site]["host"],
    )
    assert response.status_code == 302


@pytest.mark.parametrize(
    "site,path,expected",
    [
        ("portal", "/login/", "Dockside Fulfillment"),
        ("tracking", "/", "Meridian Parcel"),
        ("inbox", "/login/", "Helpline"),
        ("store", "/", "Harbor &amp; Pine"),
    ],
)
def test_host_routing(client: Client, site: str, path: str, expected: str) -> None:
    response = client.get(path, HTTP_HOST=WORLD["sites"][site]["host"])
    assert response.status_code == 200
    assert expected in response.content.decode()
    assert response.wsgi_request.urlconf == f"demo_sites.urls_{site}"


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_main_host(client: Client, host: str) -> None:
    response = client.get("/health/", HTTP_HOST=host)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_native_async_routing() -> None:
    async def downstream(request: HttpRequest) -> HttpResponse:
        return HttpResponse(request.urlconf)

    middleware = HostRoutingMiddleware(downstream)
    assert iscoroutinefunction(middleware)
    for site, config in WORLD["sites"].items():
        request = RequestFactory().get("/", HTTP_HOST=f"{config['host']}:8101")
        response = await middleware(request)
        assert response.content.decode() == f"demo_sites.urls_{site}"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "site,listing,detail",
    [
        ("portal", "/orders/", "/orders/ORD-10482/"),
        ("inbox", "/inbox/", "/inbox/A-1/"),
    ],
)
def test_login_logout(client: Client, site: str, listing: str, detail: str) -> None:
    host = WORLD["sites"][site]["host"]
    for path in ("/", listing, detail):
        assert client.get(path, HTTP_HOST=host).url == "/login/"
    credentials = WORLD["sites"][site]["login"]
    response = client.post(
        "/login/",
        {"email": credentials["username"], "password": "wrong"},
        HTTP_HOST=host,
    )
    assert response.status_code == 200
    assert "Email or password is incorrect" in response.content.decode()
    assert f"{site}_user" not in client.session
    login(client, site)
    assert client.get("/", HTTP_HOST=host).url == listing
    assert client.get(listing, HTTP_HOST=host).status_code == 200
    assert "Sign out" in client.get(listing, HTTP_HOST=host).content.decode()
    assert "_auth_user_id" not in client.session
    assert client.get("/logout/", HTTP_HOST=host).status_code == 405
    assert client.post("/logout/", HTTP_HOST=host).url == "/login/"
    assert f"{site}_user" not in client.session
    assert client.get(detail, HTTP_HOST=host).url == "/login/"


@pytest.mark.django_db
def test_session_flags_are_independent(client: Client) -> None:
    login(client, "portal")
    assert client.get("/inbox/", HTTP_HOST="inbox.localhost").url == "/login/"


@pytest.mark.django_db
def test_order_search_and_details(client: Client) -> None:
    login(client, "portal")
    host = "portal.localhost:8101"
    listing = client.get("/orders/", HTTP_HOST=host).content.decode()
    for case in WORLD["cases"]:
        assert case["order_ref"] in listing
        filtered = client.get(
            "/orders/", {"q": case["order_ref"]}, HTTP_HOST=host
        ).content.decode()
        assert filtered.count("View order</a>") == 1
        response = client.get(f"/orders/{case['order_ref']}/", HTTP_HOST=host)
        html = response.content.decode()
        assert response.status_code == 200
        for heading in (
            "Order summary",
            "Ship-to address",
            "Fulfillment",
            "Customer",
            "Internal notes",
        ):
            assert heading in html
        for value in case["ship_to"].values():
            assert escape(value) in html
        for item in case["items"]:
            assert escape(item["name"]) in html
        assert f"{case['total']} {case['currency']}" in html
        assert f"<dd>{case['customer_history']['previous_orders']}</dd>" in html
        assert "<dt>Signature required</dt><dd>No</dd>" in html
        assert escape(case["fulfillment"]["internal_notes"]) in html
        number = case["fulfillment"]["tracking_number"]
        assert f'href="http://tracking.localhost:8101/track/{number}/"' in html
        assert "order placed" not in html.lower()
    assert (
        "No orders found"
        in client.get("/orders/", {"q": "missing"}, HTTP_HOST=host).content.decode()
    )
    assert client.get("/orders/missing/", HTTP_HOST=host).status_code == 404
    secure = client.get(
        "/orders/ORD-10482/", HTTP_HOST="portal.localhost:9443", secure=True
    )
    assert b"https://tracking.localhost:9443/" in secure.content


@pytest.mark.parametrize("case", WORLD["cases"], ids=lambda case: case["key"])
def test_tracking(client: Client, case: dict[str, Any]) -> None:
    number = case["fulfillment"]["tracking_number"]
    assert (
        client.get("/", {"number": number}, HTTP_HOST="tracking.localhost").url
        == f"/track/{number}/"
    )
    response = client.get(f"/track/{number}/", HTTP_HOST="tracking.localhost")
    html = response.content.decode()
    assert response.status_code == 200
    assert case["tracking"]["summary"] in html
    assert number in html
    expected = sorted(
        case["tracking"]["events"],
        key=lambda event: timestamp(event["days_ago"], event["time"]),
        reverse=True,
    )
    rows = html.split("<tbody>")[1]
    positions = [rows.index(escape(event["status"])) for event in expected]
    assert positions == sorted(positions)
    for event in expected:
        assert display_date(timestamp(event["days_ago"], event["time"])) in html
    missing = client.get("/track/missing/", HTTP_HOST="tracking.localhost")
    assert missing.status_code == 404
    assert b"No shipment found" in missing.content


@pytest.mark.django_db
def test_mail_list_search_and_every_message(client: Client) -> None:
    login(client, "inbox")
    host = "inbox.localhost"
    listing = client.get("/inbox/", HTTP_HOST=host).content.decode()
    ordered = messages()
    positions = [listing.index(f"/inbox/{message['id']}/") for message in ordered]
    assert positions == sorted(positions)
    assert len(ordered) == sum(len(case["inbox"]) for case in WORLD["cases"])
    assert "untrusted_test" not in listing
    for message in ordered:
        response = client.get(f"/inbox/{message['id']}/", HTTP_HOST=host)
        html = response.content.decode()
        assert response.status_code == 200
        for key in ("sender", "recipient", "subject", "body", "date"):
            assert escape(message[key]) in html
        assert "untrusted_test" not in html
        assert "test metadata" not in html.lower()
    for query in ("neighbours", "Automated notice", "sarah.okafor@example.com"):
        html = client.get("/inbox/", {"q": query}, HTTP_HOST=host).content.decode()
        expected = [
            message
            for message in ordered
            if query.casefold()
            in " ".join(
                message[key] for key in ("subject", "body", "sender")
            ).casefold()
        ]
        assert html.count("<tr><td>") == len(expected)
        for message in expected:
            assert escape(message["subject"]) in html
    assert (
        b"No messages found"
        in client.get("/inbox/", {"q": "missing"}, HTTP_HOST=host).content
    )
    assert client.get("/inbox/missing/", HTTP_HOST=host).status_code == 404


def test_store(client: Client) -> None:
    host = "store.localhost"
    home = client.get("/", HTTP_HOST=host)
    for case in WORLD["cases"]:
        for item in case["items"]:
            assert escape(item["name"]) in home.content.decode()
    policy = client.get("/policy/", HTTP_HOST=host).content.decode()
    for key in ("title", "effective"):
        assert escape(WORLD["store"]["policy"][key]) in policy
    for section in WORLD["store"]["policy"]["sections"]:
        assert f'id="{section["id"]}"' in policy
        assert f'href="#{section["id"]}"' in policy
        assert escape(section["heading"]) in policy
        assert escape(section["text"]) in policy
    assert (
        WORLD["store"]["support_email"]
        in client.get("/contact/", HTTP_HOST=host).content.decode()
    )


def test_seed_command_idempotent_and_reset() -> None:
    assert not seed_path().exists()
    call_command("seed_demo_sites")
    original = seed_path().read_text()
    parsed = datetime.fromisoformat(json.loads(original)["seed_time"])
    assert parsed.tzinfo == UTC
    call_command("seed_demo_sites")
    assert seed_path().read_text() == original
    seed_path().write_text('{"seed_time": "2020-01-01T00:00:00+00:00"}')
    call_command("seed_demo_sites", reset=True)
    assert seed_time().year != 2020


def test_shared_lazy_dates(client: Client) -> None:
    seed_time()
    seed_path().write_text('{"seed_time": "2026-11-10T00:00:00+00:00"}')
    assert display_date(timestamp(5, "18:22")) == "Nov 5, 2026 6:22 PM"
    before = seed_path().read_text()
    for case in WORLD["cases"]:
        client.get(
            f"/track/{case['fulfillment']['tracking_number']}/",
            HTTP_HOST="tracking.localhost",
        )
    assert seed_path().read_text() == before
    assert (
        next(message for message in messages() if message["id"] == "A-1")["date"]
        == "Nov 5, 2026 6:22 PM"
    )
