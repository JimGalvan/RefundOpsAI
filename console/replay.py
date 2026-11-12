"""Export and replay offline investigations without a model or browser."""

import asyncio
import json
import math
import os
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone

from cases.models import AgentEvent, Case, Evidence, Investigation, Recommendation
from console.demo import is_offline
from console.hub import Hub
from console.hub import hub as default_hub
from demo_sites.data import WORLD

REPLAY_DIR = Path(settings.BASE_DIR) / "demo_data" / "replays"
_TOKEN = re.compile(r"\bE(\d+)\b")
_RECOMMENDATION_FIELDS = (
    "action", "amount", "currency", "headline", "rationale", "buyer_note", "citations",
    "contradictions", "missing", "alternatives", "confidence", "guardrails", "model",
)


def fixture_for(case: Case, directory: Path = REPLAY_DIR) -> dict | None:
    """Find the recording for a known offline demo order."""
    if not is_offline(case):
        return None
    key = next((entry["key"] for entry in WORLD["cases"] if entry["order_ref"] == case.order_ref), None)
    if key is None:
        return None
    try:
        fixture = json.loads((directory / f"{key}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return fixture if isinstance(fixture, dict) and fixture.get("order_ref") == case.order_ref else None


def replay_wanted(case: Case, directory: Path = REPLAY_DIR) -> bool:
    mode = os.environ.get("REFUNDOPS_REPLAY")
    return fixture_for(case, directory) is not None and (
        mode == "1" or (mode != "0" and not any(
            os.environ.get(key) for key in ("VULTR_INFERENCE_API_KEY", "OPENAI_API_KEY")
        ))
    )


def _remap(value: Any, ids: dict[int, int]) -> Any:
    if isinstance(value, str):
        return _TOKEN.sub(lambda match: f"E{ids.get(int(match[1]), int(match[1]))}", value)
    if isinstance(value, dict):
        return {key: _remap(item, ids) for key, item in value.items()}
    if isinstance(value, list):
        return [_remap(item, ids) for item in value]
    return value


def _delays(timeline: list[dict], speed: float) -> list[float]:
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError("Replay speed must be a positive finite number")
    previous = 0.0
    delays = []
    for item in timeline:
        current = float(item["t"])
        if not math.isfinite(current):
            raise ValueError("Replay timestamps must be finite")
        delays.append(max(0.15, min(1.0, current - previous)))
        previous = current
    scale = min(1.0, 25.0 / sum(delays)) if delays else 1.0
    return [delay * scale / speed for delay in delays]


def _jpeg_path(directory: Path, relative: str) -> Path:
    path = (directory / relative).resolve()
    if not path.is_relative_to(directory.resolve()) or path.suffix.lower() != ".jpg":
        raise ValueError("Screenshot must be a JPEG inside its recording directory")
    return path


def _copy_frame(case_id: int, investigation_id: int, source: Path) -> tuple[str, bytes]:
    jpeg = source.read_bytes()
    folder = Path(settings.MEDIA_ROOT) / "evidence" / str(case_id)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{investigation_id}-{len(list(folder.glob('*.jpg'))) + 1}.jpg"
    (folder / name).write_bytes(jpeg)
    return f"evidence/{case_id}/{name}", jpeg


async def run_replay(case_id: int, hub: Hub = default_hub, *, speed: float | None = None) -> int:
    """Replay into new audit rows, holding the running flag until the recommendation is saved."""
    if case_id in hub.running:
        raise RuntimeError("An investigation is already running for this case")
    hub.running.add(case_id)
    try:
        return await _run(case_id, hub, speed)
    finally:
        hub.running.discard(case_id)


async def _run(case_id: int, hub: Hub, speed: float | None) -> int:
    case = await Case.objects.aget(pk=case_id)
    await Investigation.objects.filter(case=case, status=Investigation.Status.RUNNING).aupdate(
        status=Investigation.Status.FAILED, error="The server stopped before this investigation finished.",
        finished_at=timezone.now(),
    )
    investigation = await Investigation.objects.acreate(case=case, strong_model="replay", fast_model="replay")
    sequence = 0

    async def log(event_type: str, payload: dict) -> None:
        nonlocal sequence
        sequence += 1
        await AgentEvent.objects.acreate(investigation=investigation, seq=sequence, type=event_type, payload=payload)
        hub.publish(case_id, "log", {"seq": sequence, "type": event_type, "payload": payload})

    try:
        case.state = Case.State.INVESTIGATING
        await case.asave(update_fields=["state", "updated_at"])
        hub.publish(case_id, "status", {"state": case.state, "investigation_id": investigation.pk})
        fixture = await asyncio.to_thread(fixture_for, case, REPLAY_DIR)
        if fixture is None:
            raise ValueError("No readable replay fixture exists for this case")
        if fixture["version"] != 1:
            raise ValueError("Unsupported replay fixture version")
        timeline = fixture["timeline"]
        delays = _delays(timeline, speed if speed is not None else float(os.environ.get("REFUNDOPS_REPLAY_SPEED", "1")))
        ids: dict[int, int] = {}
        # Show a page in the live browser view from the start instead of an empty frame.
        first_shot = next((item["screenshot"] for item in timeline if item.get("screenshot")), None)
        if first_shot:
            hub.frames[case.pk] = await asyncio.to_thread(_jpeg_path(REPLAY_DIR, first_shot).read_bytes)
        for item, delay in zip(timeline, delays, strict=True):
            await asyncio.sleep(delay)
            if item["kind"] == "event":
                await log(item["type"], _remap(item["payload"], ids))
            elif item["kind"] == "evidence":
                screenshot = ""
                if item.get("screenshot"):
                    source = _jpeg_path(REPLAY_DIR, item["screenshot"])
                    screenshot, jpeg = await asyncio.to_thread(_copy_frame, case.pk, investigation.pk, source)
                    hub.frames[case.pk] = jpeg
                evidence = await Evidence.objects.acreate(
                    case=case, investigation=investigation, kind=item["evidence_kind"], supports=item["supports"],
                    claim=item["claim"], quote=item["quote"], source_label=item["source_label"],
                    source_url=item["source_url"], screenshot=screenshot,
                )
                ids[item["ref"]] = evidence.pk
                hub.publish(case.pk, "evidence", {"id": evidence.pk})
            else:
                raise ValueError("Unknown replay timeline item kind")
        investigation.summary = fixture["summary"]
        investigation.open_questions = fixture["open_questions"]
        investigation.status = Investigation.Status.COMPLETED
        investigation.finished_at = timezone.now()
        await investigation.asave()
        await log("recommendation.started", {})
        draft = fixture["recommendation"]
        fields = {key: _remap(draft[key], ids) for key in _RECOMMENDATION_FIELDS}
        fields["citations"] = [ids[ref] for ref in draft["citations"] if ref in ids]
        fields["amount"] = Decimal(draft["amount"]) if draft["amount"] is not None else None
        # Guardrail results and model metadata describe the recorded run verbatim.
        fields["guardrails"] = draft["guardrails"]
        fields["model"] = draft["model"]
        await Recommendation.objects.acreate(case=case, investigation=investigation, **fields)
        state = Case.State.NEEDS_INFO if draft["action"] == Recommendation.Action.NEEDS_INFO else Case.State.READY
        await Case.objects.filter(pk=case_id).aupdate(state=state, updated_at=timezone.now())
    except Exception as error:
        investigation.status = Investigation.Status.FAILED
        investigation.error = f"{type(error).__name__}: {error}"[:2000]
        investigation.finished_at = timezone.now()
        await investigation.asave()
        await Case.objects.filter(pk=case_id).aupdate(state=Case.State.NEW, updated_at=timezone.now())
    await case.arefresh_from_db()
    hub.publish(case_id, "status", {"state": case.state, "investigation_id": investigation.pk, "done": True})
    return investigation.pk


def export_replay(investigation: Investigation, key: str, directory: Path = REPLAY_DIR) -> Path:
    """Save a completed investigation and its latest recommendation as a portable recording."""
    if key not in {"A", "B"}:
        raise ValueError("Replay key must be A or B")
    if investigation.status != Investigation.Status.COMPLETED:
        raise ValueError("The investigation must be completed before exporting")
    recommendation = investigation.recommendations.first()
    if recommendation is None:
        raise ValueError("The investigation has no recommendation")
    directory.mkdir(parents=True, exist_ok=True)
    timeline = []
    for event in investigation.events.all():
        timeline.append((event.created_at, 1, {
            "t": round((event.created_at - investigation.started_at).total_seconds(), 1),
            "kind": "event", "type": event.type, "payload": event.payload,
        }))
    for number, evidence in enumerate(investigation.evidence.all(), start=1):
        screenshot = None
        if evidence.screenshot:
            source = _jpeg_path(Path(settings.MEDIA_ROOT), evidence.screenshot)
            if source.is_file():
                screenshot = f"shots/{key}-{number}.jpg"
                (directory / "shots").mkdir(exist_ok=True)
                (directory / screenshot).write_bytes(source.read_bytes())
        timeline.append((evidence.captured_at, 0, {
            "t": round((evidence.captured_at - investigation.started_at).total_seconds(), 1),
            "kind": "evidence", "ref": evidence.pk, "evidence_kind": evidence.kind,
            "supports": evidence.supports, "claim": evidence.claim, "quote": evidence.quote,
            "source_label": evidence.source_label, "source_url": evidence.source_url, "screenshot": screenshot,
        }))
    timeline.sort(key=lambda entry: (entry[0], entry[1]))
    fields = {field: getattr(recommendation, field) for field in _RECOMMENDATION_FIELDS}
    fields["amount"] = str(recommendation.amount) if recommendation.amount is not None else None
    fixture = {
        "version": 1, "key": key, "order_ref": investigation.case.order_ref,
        "recorded_with": {"strong_model": investigation.strong_model, "fast_model": investigation.fast_model},
        "summary": investigation.summary, "open_questions": investigation.open_questions,
        "timeline": [entry[2] for entry in timeline], "recommendation": fields,
    }
    path = directory / f"{key}.json"
    path.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
