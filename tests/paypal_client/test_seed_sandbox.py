import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from paypal_client import PayPalClient
from scripts import seed_sandbox as seed


@pytest.fixture
def seed_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    state_path = tmp_path / "data" / "seed_state.json"
    monkeypatch.setattr(seed, "STATE_PATH", state_path)
    # The workflow tests use only injected mock HTTP, never local credentials.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    return state_path


def test_dry_run_without_credentials_or_http(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seed_state: Path,
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("dry-run must not construct a client, load dotenv, or call HTTP")

    for key in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_ENV"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(PayPalClient, "from_env", forbidden)
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr("dotenv.load_dotenv", forbidden)
    assert seed.main(["create", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "Case A:" in output and "Case B:" in output
    payload_a = json.loads(output.split("Case A:\n", 1)[1].split("Case B:\n", 1)[0])
    payload_b = json.loads(output.split("Case B:\n", 1)[1])
    assert payload_a["purchase_units"][0]["amount"]["value"] == "120.00"
    assert payload_b["purchase_units"][0]["amount"]["value"] == "90.00"
    assert (
        payload_a["purchase_units"][0]["shipping"]["name"]["full_name"]
        == "Sarah Okafor"
    )
    assert "address_line_2" not in payload_b["purchase_units"][0]["shipping"]["address"]
    assert not seed_state.exists()


def test_dry_run_case_selection_and_suffix(
    capsys: pytest.CaptureFixture[str], seed_state: Path
) -> None:
    assert seed.main(["create", "--cases", "B", "--suffix=-demo", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "Case A:" not in output
    payload = json.loads(output.split("Case B:\n", 1)[1])
    assert payload["purchase_units"][0]["invoice_id"] == "ORD-10517-demo"
    assert not seed_state.exists()


def _seeded_state(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "cases": {
                    "A": {
                        "order_id": "ORDER-A",
                        "approval_url": "https://example.com/approve-A",
                    },
                    "B": {
                        "order_id": "ORDER-B",
                        "approval_url": "https://example.com/approve-B",
                    },
                }
            }
        ),
        encoding="utf-8",
    )


def test_create_retries_duplicate_invoice_and_persists(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seed_state: Path,
) -> None:
    invoices: list[str] = []
    request_ids: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(
                200, json={"access_token": "seed-token", "expires_in": 3600}
            )
        invoice = json.loads(request.content)["purchase_units"][0]["invoice_id"]
        invoices.append(invoice)
        request_ids.append(request.headers["PayPal-Request-Id"])
        if len(invoices) < 3:
            return httpx.Response(
                422,
                json={
                    "name": "UNPROCESSABLE_ENTITY",
                    "details": [{"issue": "DUPLICATE_INVOICE_ID"}],
                },
            )
        return httpx.Response(
            201,
            json={
                "id": "ORDER-A",
                "status": "PAYER_ACTION_REQUIRED",
                "links": [
                    {"rel": "payer-action", "href": "https://example.com/approve-A"},
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as transport:
        client = PayPalClient("seed-client", "seed-secret", client=transport)
        monkeypatch.setattr(PayPalClient, "from_env", lambda: client)
        assert seed.main(["create", "--cases", "A", "--suffix=-demo"]) == 0
    assert invoices == ["ORD-10482-demo", "ORD-10482-demo-r2", "ORD-10482-demo-r3"]
    assert len(set(request_ids)) == 3
    state = json.loads(seed_state.read_text(encoding="utf-8"))
    assert state["cases"]["A"] == {
        "order_id": "ORDER-A",
        "invoice_id": "ORD-10482-demo-r3",
        "status": "PAYER_ACTION_REQUIRED",
        "approval_url": "https://example.com/approve-A",
    }
    output = capsys.readouterr().out
    assert "ORDER-A" in output and "https://example.com/approve-A" in output
    assert "seed-secret" not in output and "seed-token" not in output


@pytest.mark.parametrize("name", ["ORDER_NOT_APPROVED", "UNPROCESSABLE_ENTITY"])
def test_capture_continues_after_unapproved_order(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seed_state: Path,
    name: str,
) -> None:
    _seeded_state(seed_state)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(
                200, json={"access_token": "seed-token", "expires_in": 3600}
            )
        if request.url.path.endswith("/ORDER-A/capture"):
            return httpx.Response(422, json={"name": name})
        assert request.url.path.endswith("/ORDER-B/capture")
        return httpx.Response(
            201,
            json={
                "id": "ORDER-B",
                "status": "COMPLETED",
                "purchase_units": [
                    {
                        "payments": {
                            "captures": [
                                {
                                    "id": "CAPTURE-B",
                                    "status": "COMPLETED",
                                    "amount": {
                                        "value": "90.00",
                                        "currency_code": "USD",
                                    },
                                }
                            ]
                        },
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as transport:
        monkeypatch.setattr(
            PayPalClient,
            "from_env",
            lambda: PayPalClient("id", "secret", client=transport),
        )
        assert seed.main(["capture"]) == 0
    state = json.loads(seed_state.read_text(encoding="utf-8"))
    assert "capture_id" not in state["cases"]["A"]
    assert state["cases"]["B"]["capture_id"] == "CAPTURE-B"
    output = capsys.readouterr().out
    assert "A: not approved yet" in output
    assert "B: CAPTURE-B  COMPLETED  90.00 USD" in output


def test_status_refreshes_remote_orders(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seed_state: Path,
) -> None:
    _seeded_state(seed_state)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(
                200, json={"access_token": "seed-token", "expires_in": 3600}
            )
        assert request.method == "GET"
        return httpx.Response(200, json={"status": "APPROVED"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as transport:
        monkeypatch.setattr(
            PayPalClient,
            "from_env",
            lambda: PayPalClient("id", "secret", client=transport),
        )
        assert seed.main(["status"]) == 0
    output = capsys.readouterr().out
    assert "A: ORDER-A  APPROVED  capture=-" in output
    assert "B: ORDER-B  APPROVED  capture=-" in output


def test_empty_state_does_not_need_credentials(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seed_state: Path,
) -> None:
    def forbidden() -> None:
        pytest.fail("empty status must not need credentials")

    monkeypatch.setattr(PayPalClient, "from_env", forbidden)
    assert seed.main(["status"]) == 0
    assert "No seeded orders" in capsys.readouterr().out


def test_invalid_case_selection() -> None:
    with pytest.raises(SystemExit) as raised:
        seed.main(["create", "--cases", "C", "--dry-run"])
    assert raised.value.code == 2
