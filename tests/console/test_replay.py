"""Offline replay uses real audit rows and local files, with no agent dependencies."""

import json
from decimal import Decimal
from functools import partial
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from asgiref.sync import async_to_sync
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from cases.models import AgentEvent, Case, Evidence, Investigation, Recommendation
from console import replay
from console.hub import Hub

pytestmark = pytest.mark.django_db(transaction=True)
JPEG = b"\xff\xd8tiny jpeg\xff\xd9"


@pytest.fixture
def recording(settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    settings.MEDIA_ROOT = tmp_path / "media"
    directory = tmp_path / "replays"
    monkeypatch.setattr(replay, "REPLAY_DIR", directory)
    case = Case.objects.create(paypal_dispute_id="DEMO-A", order_ref="ORD-10482", amount=Decimal("120"))
    investigation = Investigation.objects.create(
        case=case, status=Investigation.Status.COMPLETED, strong_model="recorded-strong",
        fast_model="recorded-fast", summary="The parcel came back.", open_questions=["When?"],
        finished_at=timezone.now(),
    )
    first_event = AgentEvent.objects.create(
        investigation=investigation, seq=1, type="browser.navigation.started", payload={"url": "http://carrier.test/"},
    )
    evidence = []
    for index, kind in enumerate(["tracking", "policy", "message"], start=1):
        screenshot = f"evidence/{case.pk}/recorded-{index}.jpg"
        path = settings.MEDIA_ROOT / screenshot
        path.parent.mkdir(parents=True, exist_ok=True)
        if index < 3:
            path.write_bytes(JPEG)
        evidence.append(Evidence.objects.create(
            case=case, investigation=investigation, kind=kind, supports="buyer",
            claim=f"Claim {index}", quote=f"Quote {index}", source_label="Carrier", source_url="http://carrier.test/",
            screenshot=screenshot,
        ))
    a, b, c = [item.pk for item in evidence]
    last_event = AgentEvent.objects.create(
        investigation=investigation, seq=2, type="agent.message",
        payload={"text": f"E{a} and E{b}", "nested": [{"quote": f"E{c}"}], "number": a},
    )
    Recommendation.objects.create(
        case=case, investigation=investigation, action="ACCEPT_CLAIM", amount=Decimal("120.00"), currency="USD",
        headline=f"Refund E{a}", rationale=f"E{a} and E{b} support E{c}.", buyer_note=f"Seen in E{c}.",
        citations=[a, b, c, 999999], contradictions=[{"note": f"E{a}", "nested": [f"E{b}"]}],
        missing=[f"E{c}"], alternatives=[{"why_not": f"E{b}"}], confidence="high",
        guardrails={"ok": True, "violations": []}, model="recorded-strong",
    )
    # Rounded timestamps coincide; the exporter must use the original timestamp to order them.
    start = investigation.started_at
    AgentEvent.objects.filter(pk=first_event.pk).update(created_at=start + timezone.timedelta(seconds=0.04))
    Evidence.objects.filter(pk=a).update(captured_at=start + timezone.timedelta(seconds=0.03))
    Evidence.objects.filter(pk=b).update(captured_at=start + timezone.timedelta(seconds=0.041))
    Evidence.objects.filter(pk=c).update(captured_at=start + timezone.timedelta(seconds=0.042))
    AgentEvent.objects.filter(pk=last_event.pk).update(created_at=start + timezone.timedelta(seconds=0.043))
    path = replay.export_replay(investigation, "A", directory)
    target = Case.objects.create(paypal_dispute_id="DEMO-COPY", order_ref=case.order_ref, amount=case.amount)
    return {"source": investigation, "case": target, "evidence": evidence, "directory": directory, "path": path}


def play(case: Case, hub: Hub | None = None) -> Investigation:
    pk = async_to_sync(replay.run_replay)(case.pk, hub or Hub(), speed=1_000_000)
    return Investigation.objects.get(pk=pk)


def test_round_trip(recording: dict, settings) -> None:
    case = recording["case"]
    hub = Hub()
    queue = hub.subscribe(case.pk)
    original_publish = hub.publish

    def publish(case_id: int, name: str, payload: dict) -> None:
        assert case_id in hub.running
        original_publish(case_id, name, payload)

    hub.publish = publish
    investigation = play(case, hub)
    assert investigation.status == Investigation.Status.COMPLETED
    assert investigation.strong_model == investigation.fast_model == "replay"
    assert investigation.summary == recording["source"].summary
    assert investigation.open_questions == ["When?"]
    assert investigation.finished_at is not None
    evidence = list(investigation.evidence.all())
    assert [item.claim for item in evidence] == [item.claim for item in recording["evidence"]]
    assert [item.quote for item in evidence] == [item.quote for item in recording["evidence"]]
    assert [item.kind for item in evidence] == ["tracking", "policy", "message"]
    for item in evidence[:2]:
        assert (Path(settings.MEDIA_ROOT) / item.screenshot).read_bytes() == JPEG
        assert item.screenshot.startswith(f"evidence/{case.pk}/{investigation.pk}-")
    assert evidence[2].screenshot == ""
    assert hub.frames[case.pk] == JPEG
    a, b, c = [item.pk for item in evidence]
    recommendation = case.recommendations.get()
    assert recommendation.investigation_id == investigation.pk
    assert recommendation.citations == [a, b, c]
    assert recommendation.headline == f"Refund E{a}"
    assert recommendation.rationale == f"E{a} and E{b} support E{c}."
    assert recommendation.buyer_note == f"Seen in E{c}."
    assert recommendation.contradictions == [{"note": f"E{a}", "nested": [f"E{b}"]}]
    assert recommendation.missing == [f"E{c}"]
    assert recommendation.alternatives == [{"why_not": f"E{b}"}]
    assert recommendation.guardrails == {"ok": True, "violations": []}
    assert recommendation.amount == Decimal("120.00")
    assert recommendation.model == "recorded-strong"
    events = list(investigation.events.all())
    assert [item.type for item in events] == ["browser.navigation.started", "agent.message", "recommendation.started"]
    assert [item.seq for item in events] == [1, 2, 3]
    assert events[1].payload["text"] == f"E{a} and E{b}"
    assert events[1].payload["nested"] == [{"quote": f"E{c}"}]
    case.refresh_from_db()
    assert case.state == Case.State.READY
    assert not hub.running
    messages = []
    while not queue.empty():
        messages.append(queue.get_nowait())
    assert messages[0][0] == "status"
    assert messages[-1] == ("status", {"state": "ready", "investigation_id": investigation.pk, "done": True})
    assert [data["id"] for name, data in messages if name == "evidence"] == [a, b, c]
    assert [data["seq"] for name, data in messages if name == "log"] == [1, 2, 3]
    fixture = json.loads(recording["path"].read_text(encoding="utf-8"))
    assert fixture["timeline"][0]["kind"] == "evidence"
    assert fixture["timeline"][1]["kind"] == "event"
    assert fixture["timeline"][0]["t"] == fixture["timeline"][1]["t"] == 0.0
    assert fixture["timeline"][-2]["screenshot"] is None
    assert fixture["recorded_with"] == {"strong_model": "recorded-strong", "fast_model": "recorded-fast"}


def test_needs_info(recording: dict) -> None:
    fixture = json.loads(recording["path"].read_text(encoding="utf-8"))
    fixture["recommendation"].update(action="NEEDS_INFO", amount=None)
    recording["path"].write_text(json.dumps(fixture), encoding="utf-8")
    play(recording["case"])
    recording["case"].refresh_from_db()
    assert recording["case"].state == Case.State.NEEDS_INFO
    assert recording["case"].recommendations.get().amount is None


def test_fixture_lookup(recording: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case, directory = recording["case"], recording["directory"]
    assert replay.fixture_for(case, directory)["key"] == "A"
    assert replay.fixture_for(case, tmp_path / "missing") is None
    case.paypal_dispute_id = "REAL-A"
    assert replay.fixture_for(case, directory) is None
    case.paypal_dispute_id = "DEMO-A"
    case.order_ref = "UNKNOWN"
    assert replay.fixture_for(case, directory) is None
    case.order_ref = "ORD-10482"
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("denied")))
    assert replay.fixture_for(case, directory) is None


@pytest.mark.parametrize("mode", ["1", "0", None])
@pytest.mark.parametrize("keys", [(), ("OPENAI_API_KEY",), ("VULTR_INFERENCE_API_KEY",),
                                  ("OPENAI_API_KEY", "VULTR_INFERENCE_API_KEY")])
def test_replay_wanted(recording: dict, monkeypatch: pytest.MonkeyPatch, mode: str | None, keys: tuple) -> None:
    for key in ("REFUNDOPS_REPLAY", "OPENAI_API_KEY", "VULTR_INFERENCE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    if mode is not None:
        monkeypatch.setenv("REFUNDOPS_REPLAY", mode)
    for key in keys:
        monkeypatch.setenv(key, "fake-key")
    assert replay.replay_wanted(recording["case"], recording["directory"]) == (
        mode == "1" or (mode is None and not keys)
    )
    assert not replay.replay_wanted(recording["case"], recording["directory"] / "absent")


def test_empty_keys_do_not_disable_replay(recording: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REFUNDOPS_REPLAY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("VULTR_INFERENCE_API_KEY", "")
    assert replay.replay_wanted(recording["case"], recording["directory"])


def test_replay_twice_closes_stale_runs(recording: dict) -> None:
    case = recording["case"]
    stale = Investigation.objects.create(case=case)
    first = play(case)
    second = play(case)
    assert first.pk != second.pk
    assert case.investigations.filter(status=Investigation.Status.COMPLETED).count() == 2
    stale.refresh_from_db()
    assert stale.status == Investigation.Status.FAILED
    assert stale.error == "The server stopped before this investigation finished."
    assert stale.finished_at is not None


def test_refuses_running_case(recording: dict) -> None:
    case = recording["case"]
    hub = Hub()
    hub.running.add(case.pk)
    with pytest.raises(RuntimeError, match="already running"):
        play(case, hub)
    assert not case.investigations.exists()
    assert hub.running == {case.pk}


@pytest.mark.parametrize("contents", ["{", "{}", "[]", None])
def test_bad_fixture_fails(recording: dict, contents: str | None) -> None:
    if contents is None:
        recording["path"].unlink()
    else:
        recording["path"].write_text(contents, encoding="utf-8")
    hub = Hub()
    investigation = play(recording["case"], hub)
    assert investigation.status == Investigation.Status.FAILED
    assert investigation.error.startswith(("ValueError:", "KeyError:"))
    assert investigation.finished_at is not None
    recording["case"].refresh_from_db()
    assert recording["case"].state == Case.State.NEW
    assert not recording["case"].recommendations.exists()
    assert not hub.running


def test_export_rejects_invalid_runs(recording: dict) -> None:
    investigation = recording["source"]
    with pytest.raises(ValueError, match="key must"):
        replay.export_replay(investigation, "../bad", recording["directory"])
    investigation.status = Investigation.Status.RUNNING
    with pytest.raises(ValueError, match="must be completed"):
        replay.export_replay(investigation, "A", recording["directory"])
    investigation.status = Investigation.Status.COMPLETED
    investigation.recommendations.all().delete()
    with pytest.raises(ValueError, match="no recommendation"):
        replay.export_replay(investigation, "A", recording["directory"])


def test_unreadable_fixture_fails(recording: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    def unreadable(*args: object, **kwargs: object) -> str:
        raise PermissionError("Fixture cannot be read")

    monkeypatch.setattr(Path, "read_text", unreadable)
    hub = Hub()
    investigation = play(recording["case"], hub)
    assert investigation.status == Investigation.Status.FAILED
    assert investigation.error.startswith("ValueError:")
    recording["case"].refresh_from_db()
    assert recording["case"].state == Case.State.NEW
    assert not hub.running


def test_recommendation_failure_fails_run(recording: dict) -> None:
    fixture = json.loads(recording["path"].read_text(encoding="utf-8"))
    fixture["recommendation"]["amount"] = "invalid"
    recording["path"].write_text(json.dumps(fixture), encoding="utf-8")
    hub = Hub()
    investigation = play(recording["case"], hub)
    assert investigation.status == Investigation.Status.FAILED
    assert investigation.error.startswith("InvalidOperation:")
    recording["case"].refresh_from_db()
    assert recording["case"].state == Case.State.NEW
    assert not recording["case"].recommendations.exists()
    assert not hub.running


def test_export_evidence_before_event_at_equal_timestamp(recording: dict) -> None:
    investigation = recording["source"]
    investigation.events.update(created_at=investigation.started_at)
    investigation.evidence.update(captured_at=investigation.started_at)
    path = replay.export_replay(investigation, "A", recording["directory"])
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert [item["kind"] for item in fixture["timeline"]] == ["evidence"] * 3 + ["event"] * 2
    assert all(item["t"] == 0.0 for item in fixture["timeline"])


def test_command(recording: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("console.management.commands.export_replay.export_replay",
                        partial(replay.export_replay, directory=recording["directory"]))
    output = StringIO()
    case = recording["source"].case
    # A later completed run without a recommendation must not hide the exportable one.
    Investigation.objects.create(case=case, status=Investigation.Status.COMPLETED)
    call_command("export_replay", case.pk, "A", stdout=output)
    assert str(recording["path"]) in output.getvalue()
    assert "2 events, 3 evidence items, 2 screenshots" in output.getvalue()
    call_command("export_replay", case.pk, "B", investigation=recording["source"].pk, stdout=output)
    assert (recording["directory"] / "B.json").is_file()
    for case_id, key, options, message in [
        (999999, "A", {}, "case does not exist"),
        (case.pk, "C", {}, "key must"),
        (case.pk, "A", {"investigation": 999999}, "investigation does not exist"),
        (recording["case"].pk, "A", {}, "No completed investigation"),
    ]:
        with pytest.raises(CommandError, match=message):
            call_command("export_replay", case_id, key, **options)


def test_remapping_is_one_pass_and_preserves_unknown_refs() -> None:
    assert replay._remap({"nested": ["E1 E2 E999 XE1 E1x", 1]}, {1: 2, 2: 3}) == {
        "nested": ["E2 E3 E999 XE1 E1x", 1],
    }


def test_pacing_and_array_order(recording: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    assert replay._delays([{"t": 0}, {"t": 10}, {"t": 10.1}], 2) == pytest.approx([0.075, 0.5, 0.075])
    assert sum(replay._delays([{"t": n * 2} for n in range(50)], 1)) == pytest.approx(25)
    fixture = json.loads(recording["path"].read_text(encoding="utf-8"))
    # An event referring to evidence still in the future keeps that original token.
    old_id = recording["evidence"][0].pk
    fixture["timeline"] = [
        {"t": 9, "kind": "event", "type": "agent.message", "payload": {"text": f"E{old_id}"}},
        *fixture["timeline"],
    ]
    recording["path"].write_text(json.dumps(fixture), encoding="utf-8")
    sleep = AsyncMock()
    monkeypatch.setattr(replay.asyncio, "sleep", sleep)
    monkeypatch.setenv("REFUNDOPS_REPLAY_SPEED", "1000000")
    pk = async_to_sync(replay.run_replay)(recording["case"].pk, Hub())
    investigation = Investigation.objects.get(pk=pk)
    assert investigation.events.first().payload == {"text": f"E{old_id}"}
    assert sleep.await_count == len(fixture["timeline"])
    assert sleep.await_args_list[0].args[0] == pytest.approx(0.000001)
