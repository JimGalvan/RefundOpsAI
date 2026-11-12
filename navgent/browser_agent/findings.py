import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from navgent.browser.browser_session import BrowserSession
from navgent.browser.models import Box
from navgent.browser.recording import wall_time_ms

STABILITY_CHECK_SECONDS = 0.2
QUOTE_CHARACTER_LIMIT = 120


@dataclass(frozen=True)
class LocatedQuote:
    quote: str
    box: Box


def candidate_quotes(observed: str) -> list[list[str]]:
    """Try the whole quote first, then each `; `-separated part, like the verdict check does."""
    whole = observed.strip()
    parts = []
    for part in observed.split(";"):
        if part.strip():
            parts.append(part.strip())
    if len(parts) <= 1:
        return [[whole]]
    return [[whole], parts]


async def locate_quotes(browser: BrowserSession, observed: str) -> list[LocatedQuote]:
    for quotes in candidate_quotes(observed):
        located = []
        for quote in quotes:
            box = await browser.locate_quote(quote)
            if box is not None:
                located.append(LocatedQuote(quote, box))
        if located:
            return located
    return []


async def locate_stable_quotes(browser: BrowserSession, observed: str) -> tuple[list[LocatedQuote], float, float]:
    """Measure twice and keep only boxes that did not move, with the interval they were checked over."""
    started_at = wall_time_ms()
    first = await locate_quotes(browser, observed)
    if not first:
        return [], started_at, wall_time_ms()
    await asyncio.sleep(STABILITY_CHECK_SECONDS)
    second = await locate_quotes(browser, observed)
    ended_at = wall_time_ms()
    stable = []
    for located in first:
        if located in second:
            stable.append(located)
    return stable, started_at, ended_at


def shorten(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= QUOTE_CHARACTER_LIMIT:
        return text
    return text[:QUOTE_CHARACTER_LIMIT - 1].rstrip() + "…"


def explain(quote: str, expected: str) -> str:
    shown = shorten(quote).rstrip(".")
    wanted = shorten(expected).rstrip(".")
    if not wanted:
        return f"Shows {shown}."
    return f"Shows {shown}. Expected {wanted}."


async def finding_payloads(
        browser: BrowserSession, verdict_id: str, observed: str, expected: str, step_number: int | None,
) -> list[dict[str, Any]]:
    located, started_at, ended_at = await locate_stable_quotes(browser, observed)
    if not located:
        return []

    viewport_width, viewport_height = browser.viewport_size()
    recorder = browser.current_recording()
    recording_id = None
    start_recording_time = None
    end_recording_time = None
    if recorder is not None:
        recording_id = recorder.id
        start_recording_time = recorder.recording_time_ms(started_at)
        end_recording_time = recorder.recording_time_ms(ended_at)

    payloads = []
    for number, item in enumerate(located, start=1):
        payloads.append({
            "annotation_id": uuid4().hex,
            "verdict_id": verdict_id,
            "number": number,
            "explanation": explain(item.quote, expected),
            "brief_step": step_number,
            "recording_id": recording_id,
            "x": item.box.x,
            "y": item.box.y,
            "width": item.box.width,
            "height": item.box.height,
            "viewport_width": viewport_width,
            "viewport_height": viewport_height,
            "start_wall_time_ms": started_at,
            "end_wall_time_ms": ended_at,
            "start_recording_time_ms": start_recording_time,
            "end_recording_time_ms": end_recording_time,
        })
    return payloads
