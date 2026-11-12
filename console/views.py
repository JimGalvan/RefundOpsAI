"""The merchant console: queue, case workspace, live investigation feed, decisions, sources."""

import asyncio
import contextvars
import logging
from decimal import Decimal, InvalidOperation

from asgiref.sync import sync_to_async
from django.db.models import F
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from cases.executor import ExecutionError, decide, execute_decision
from cases.models import AgentEvent, Case, Decision, Evidence, Investigation
from cases.sync import sync_disputes
from console.demo import is_offline
from console.hub import hub
from console.investigation import run_investigation
from console.presenters import case_context, case_row, describe_event, group_evidence
from console.recommend import generate_recommendation
from demo_sites.data import WORLD
from paypal_client import PayPalClient, PayPalError
from vault.models import Credential

logger = logging.getLogger(__name__)

_background: set[asyncio.Task] = set()
# INVESTIGATING is included for a run the server lost (restart or crash): hub.running says nothing is running.
INVESTIGABLE = {Case.State.NEW, Case.State.READY, Case.State.NEEDS_INFO, Case.State.FAILED, Case.State.INVESTIGATING}


def _queue_rows() -> list[dict]:
    cases = Case.objects.order_by(F("response_due_at").asc(nulls_last=True), "-created_at")
    return [case_row(case) for case in cases]


def queue(request: HttpRequest) -> HttpResponse:
    return render(request, "console/queue.html", {"rows": _queue_rows(), "active": "queue"})


@require_POST
def case_sync(request: HttpRequest) -> HttpResponse:
    try:
        result = sync_disputes(PayPalClient.from_env())
        message = f"Synced from PayPal: {len(result.created)} new, {len(result.updated)} updated."
        if result.errors:
            message += f" {len(result.errors)} could not be read: {result.errors[0]}"
    except PayPalError as error:
        message = f"PayPal answered with an error: {error}"
    except Exception as error:  # missing keys and network problems should read as a message, not a crash
        logger.warning("Sync failed: %s", error)
        message = "Could not sync. Check PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in .env, then try again."
    return render(request, "console/_queue.html", {"rows": _queue_rows(), "message": message})


def _render_case(request: HttpRequest, case: Case, **extra) -> HttpResponse:
    context = case_context(case) | extra | {"active": "queue"}
    template = "console/_case_body.html" if request.headers.get("HX-Request") else "console/case.html"
    return render(request, template, context)


def case_detail(request: HttpRequest, pk: int) -> HttpResponse:
    return _render_case(request, get_object_or_404(Case, pk=pk))


async def _wait_for_new_investigation(case_id: int, previous: int | None) -> None:
    for _ in range(60):
        newest = await Investigation.objects.filter(case_id=case_id).order_by("-pk").values_list("pk", flat=True).afirst()
        if newest is not None and newest != previous:
            return
        await asyncio.sleep(0.05)


async def case_investigate(request: HttpRequest, pk: int) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse(status=405)
    case = await Case.objects.aget(pk=pk)
    if pk not in hub.running and case.state in INVESTIGABLE:
        previous = await Investigation.objects.filter(case_id=pk).order_by("-pk").values_list("pk", flat=True).afirst()
        # A fresh context: the task outlives this request, and asgiref ties ORM calls to the request's executor.
        task = asyncio.create_task(
            run_investigation(pk, hub, after_success=generate_recommendation), context=contextvars.Context()
        )
        _background.add(task)
        task.add_done_callback(_background.discard)
        await _wait_for_new_investigation(pk, previous)
    case = await Case.objects.aget(pk=pk)
    return await sync_to_async(_render_case)(request, case)


@require_GET
def case_feed(request: HttpRequest, pk: int) -> HttpResponse:
    """New log lines and evidence since the cursors, plus the next poller while the run is still going."""
    investigation = get_object_or_404(Investigation, pk=int(request.GET.get("inv", 0)), case_id=pk)
    after = int(request.GET.get("after", 0))
    evidence_after = int(request.GET.get("ev", 0))
    events = list(AgentEvent.objects.filter(investigation=investigation, seq__gt=after))
    evidence = list(Evidence.objects.filter(investigation=investigation, pk__gt=evidence_after))
    investigation.refresh_from_db()
    done = investigation.status != Investigation.Status.RUNNING and pk not in hub.running
    started = investigation.started_at
    lines = []
    for event in events:
        described = describe_event(event)
        if described:
            seconds = int((event.created_at - started).total_seconds())
            lines.append(described | {"t": f"{seconds}s"})
    response = render(
        request,
        "console/_feed.html",
        {
            "case_id": pk,
            "inv": investigation.pk,
            "lines": lines,
            "entries": [entry for group in group_evidence(evidence) for entry in group["entries"]],
            "after": events[-1].seq if events else after,
            "ev": evidence[-1].pk if evidence else evidence_after,
            "done": done,
        },
    )
    if done:
        response["HX-Trigger"] = "refreshCase"
    return response


@require_GET
def case_frame(request: HttpRequest, pk: int) -> HttpResponse:
    frame = hub.frames.get(pk)
    if not frame:
        return HttpResponse(status=204)
    response = HttpResponse(frame, content_type="image/jpeg")
    response["Cache-Control"] = "no-store"
    return response


def _parse_amount(raw: str | None) -> Decimal | None:
    if not raw or not raw.strip():
        return None
    try:
        return Decimal(raw.replace("$", "").replace(",", "").strip()).quantize(Decimal("0.01"))
    except InvalidOperation as error:
        raise ExecutionError("The amount is not a number") from error


@require_POST
def case_decide(request: HttpRequest, pk: int) -> HttpResponse:
    case = get_object_or_404(Case, pk=pk)
    recommendation = case.recommendations.order_by("-pk").first()
    if recommendation is None:
        raise Http404("This case has no recommendation yet")
    outcome = request.POST.get("outcome", "")
    note = request.POST.get("note", "").strip()
    extra: dict = {}
    try:
        if outcome == "approve":
            decision = decide(case, recommendation, Decision.Outcome.APPROVED, note=note)
        elif outcome == "edit":
            decision = decide(
                case,
                recommendation,
                Decision.Outcome.EDITED,
                action=request.POST.get("action") or None,
                amount=_parse_amount(request.POST.get("amount")),
                note=note,
            )
        elif outcome == "reject":
            decision = decide(case, recommendation, Decision.Outcome.REJECTED, note=note)
        else:
            return HttpResponseBadRequest("Unknown outcome")
        if decision.outcome != Decision.Outcome.REJECTED:
            if is_offline(case):
                extra["notice"] = "This is an offline demo case, so nothing was sent to PayPal."
            else:
                execute_decision(decision, PayPalClient.from_env())
    except ExecutionError as error:
        extra["error"] = str(error)
    except PayPalError as error:
        extra["error"] = f"PayPal answered with an error: {error}"
    except Exception as error:
        logger.exception("Decision failed")
        extra["error"] = f"Something went wrong: {type(error).__name__}"
    case.refresh_from_db()
    return _render_case(request, case, **extra)


def sources(request: HttpRequest) -> HttpResponse:
    sites = [
        {"key": key, "brand": site["brand"], "host": site["host"], "login": "login" in site}
        for key, site in WORLD["sites"].items()
    ]
    credentials = list(Credential.objects.all())
    return render(request, "console/sources.html", {"sites": sites, "credentials": credentials, "active": "sources"})
