"""Runs one investigation: starts the browser agent, stores what it records and feeds the live view."""

import asyncio
import dataclasses
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.utils import timezone
from langchain_core.callbacks import get_usage_metadata_callback

from cases.models import AgentEvent, Case, Evidence, Investigation
from cases.paypal_record import build_paypal_record
from console.hub import Hub, hub as default_hub
from investigator.agent import make_agent_builder
from investigator.config import agent_settings, model_names
from investigator.credentials import HostRoutedCredentialProvider
from investigator.evidence import EvidenceItem
from investigator.sites import CREDENTIAL_NAMES, build_sites
from investigator.task import build_task
from investigator.tools import InvestigationContext
from navgent import BrowserAgentSession
from navgent.events import (
    AgentMessageDeltaEvent,
    InputRequiredEvent,
    RunCancelledEvent,
    RunCompletedEvent,
    RunFailedEvent,
    SensitiveInputRequiredEvent,
)
from vault.models import Credential
from vault.provider import DjangoCredentialProvider

logger = logging.getLogger(__name__)

NO_HUMAN_ANSWER = (
    "No one is available to answer questions during an investigation. Continue with the tools listed in the task "
    "and put anything you could not find in open_questions."
)
MIN_EVIDENCE_TO_COMPLETE = 3

AfterSuccess = Callable[[Case, Investigation], Awaitable[None]]
Echo = Callable[[str], None]


def _jsonable(value: object) -> dict:
    return json.loads(json.dumps(value, default=str))


class InvestigationSink:
    """Stores recorded evidence and the final summary, and tells the live view about them."""

    def __init__(self, case: Case, investigation: Investigation, hub: Hub, echo: Echo | None) -> None:
        self._case = case
        self._investigation = investigation
        self._hub = hub
        self._echo = echo
        self.summary = ""
        self.open_questions: list[str] = []

    async def add_evidence(self, item: EvidenceItem) -> int:
        screenshot = ""
        if item.screenshot:
            screenshot = await asyncio.to_thread(self._save_screenshot, item.screenshot)
        evidence = await Evidence.objects.acreate(
            case=self._case,
            investigation=self._investigation,
            kind=item.kind,
            supports=item.supports,
            claim=item.claim,
            quote=item.quote,
            source_label=item.source_label,
            source_url=item.source_url,
            screenshot=screenshot,
        )
        self._hub.publish(self._case.pk, "evidence", {"id": evidence.pk})
        if self._echo:
            self._echo(f"  E{evidence.pk} [{item.kind}/{item.supports}] {item.claim}")
        return evidence.pk

    async def finish(self, summary: str, open_questions: list[str]) -> None:
        self.summary = summary
        self.open_questions = open_questions

    def _save_screenshot(self, jpeg: bytes) -> str:
        folder = Path(settings.MEDIA_ROOT) / "evidence" / str(self._case.pk)
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{self._investigation.pk}-{len(list(folder.glob('*.jpg'))) + 1}.jpg"
        (folder / name).write_bytes(jpeg)
        return f"evidence/{self._case.pk}/{name}"


async def _credential_setup() -> tuple[dict[str, UUID], dict[str, UUID]]:
    """Vault credential ids by site key (only sites that have one saved)."""
    ids: dict[str, UUID] = {}
    for key, name in CREDENTIAL_NAMES.items():
        credential = await Credential.objects.filter(name=name).afirst()
        if credential is not None:
            ids[key] = credential.pk
    return ids, {key: ids[key] for key in ids}


async def run_investigation(
    case_id: int,
    hub: Hub = default_hub,
    *,
    after_success: AfterSuccess | None = None,
    echo: Echo | None = None,
) -> int:
    """Investigate one case. Returns the investigation id. Never raises for an agent failure."""
    if case_id in hub.running:
        raise RuntimeError("An investigation is already running for this case")
    case = await Case.objects.aget(pk=case_id)
    strong, fast, _ = model_names()
    investigation = await Investigation.objects.acreate(case=case, strong_model=strong, fast_model=fast)
    hub.running.add(case_id)
    case.state = Case.State.INVESTIGATING
    await case.asave(update_fields=["state", "updated_at"])
    hub.publish(case_id, "status", {"state": case.state, "investigation_id": investigation.pk})

    sink = InvestigationSink(case, investigation, hub, echo)
    evidence_count = 0
    usage: dict = {}
    try:
        with get_usage_metadata_callback() as usage_callback:
            await _drive_agent(case, investigation, sink, hub, echo)
        usage = _jsonable(usage_callback.usage_metadata)
        evidence_count = await Evidence.objects.filter(investigation=investigation).acount()
        if investigation.status == Investigation.Status.RUNNING:
            if evidence_count >= MIN_EVIDENCE_TO_COMPLETE:
                investigation.status = Investigation.Status.COMPLETED
            else:
                investigation.status = Investigation.Status.FAILED
                investigation.error = investigation.error or "The agent stopped before recording enough evidence."
    except Exception as error:  # an investigation failing must never take the server down
        logger.exception("Investigation %s failed", investigation.pk)
        investigation.status = Investigation.Status.FAILED
        investigation.error = f"{type(error).__name__}: {error}"[:2000]
    finally:
        hub.running.discard(case_id)
        investigation.summary = sink.summary
        investigation.open_questions = sink.open_questions
        investigation.usage = usage
        investigation.finished_at = timezone.now()
        await investigation.asave()

    case.state = Case.State.NEW
    await case.asave(update_fields=["state", "updated_at"])
    if investigation.status == Investigation.Status.COMPLETED and after_success is not None:
        try:
            await after_success(case, investigation)
        except Exception as error:
            logger.exception("Recommendation after investigation %s failed", investigation.pk)
            investigation.error = f"Recommendation failed: {type(error).__name__}: {error}"[:2000]
            await investigation.asave(update_fields=["error"])
    await case.arefresh_from_db()
    hub.publish(case_id, "status", {"state": case.state, "investigation_id": investigation.pk, "done": True})
    return investigation.pk


async def _drive_agent(
    case: Case, investigation: Investigation, sink: InvestigationSink, hub: Hub, echo: Echo | None
) -> None:
    credential_ids, _ = await _credential_setup()
    sites = build_sites(case.order_ref, credential_ids)
    providers = {
        site.host: DjangoCredentialProvider([site.credential_id], site.host) for site in sites if site.credential_id
    }
    provider = HostRoutedCredentialProvider(providers) if providers else None
    context = InvestigationContext(paypal_record=build_paypal_record(case), sink=sink)
    task = build_task(case.order_ref, context.paypal_record, sites)

    session = BrowserAgentSession(
        agent_settings(),
        credential_provider=provider,
        selected_credential_ids=tuple(site.credential_id for site in sites if site.credential_id),
        agent_builder=make_agent_builder(context),
    )
    sequence = 0
    pending_text: list[str] = []

    async def store(event_type: str, payload: dict) -> None:
        nonlocal sequence
        sequence += 1
        await AgentEvent.objects.acreate(investigation=investigation, seq=sequence, type=event_type, payload=payload)
        hub.publish(case.pk, "log", {"seq": sequence, "type": event_type, "payload": payload})
        if echo:
            echo(f"[{sequence}] {event_type} {json.dumps(payload, default=str)[:160]}")

    async def flush_text() -> None:
        if pending_text:
            text = "".join(pending_text).strip()
            pending_text.clear()
            if text:
                await store("agent.message", {"text": text})

    async def pump_frames() -> None:
        async for frame in session.screen_frames():
            hub.frames[case.pk] = frame

    async with session:
        await session.start(task)
        frames = asyncio.create_task(pump_frames())
        try:
            async for event in session.events():
                if isinstance(event, AgentMessageDeltaEvent):
                    pending_text.append(event.text)
                    continue
                await flush_text()
                payload = _jsonable(dataclasses.asdict(event))
                payload.pop("event_type", None)
                await store(event.event_type, payload)
                if isinstance(event, InputRequiredEvent):
                    await session.answer(NO_HUMAN_ANSWER)
                elif isinstance(event, SensitiveInputRequiredEvent):
                    await session.decline_sensitive_input(event.request_id)
                elif isinstance(event, RunCompletedEvent):
                    if not sink.summary:
                        sink.summary = event.output
                    investigation.status = Investigation.Status.RUNNING  # decided by the evidence count afterwards
                    break
                elif isinstance(event, (RunFailedEvent, RunCancelledEvent)):
                    investigation.status = Investigation.Status.FAILED
                    investigation.error = getattr(event, "error", "") or "The investigation was cancelled."
                    break
        finally:
            frames.cancel()
            await flush_text()
