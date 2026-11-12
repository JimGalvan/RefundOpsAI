"""Tools only the investigator has: record_evidence and finish_investigation."""

from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from demo_sites.data import WORLD
from investigator.evidence import (
    REQUIRED_KINDS,
    EvidenceItem,
    EvidenceKind,
    EvidenceSink,
    Supports,
    check_quote,
    normalize,
)
from navgent.browser.browser_session import BrowserSession

SCREENSHOT_QUALITY = 80


class EvidenceInput(BaseModel):
    kind: EvidenceKind
    claim: str = Field(min_length=3, max_length=400)
    quote: str = Field(min_length=3, max_length=600)
    supports: Supports = "neutral"
    source: Literal["page", "paypal"] = "page"


class FinishInput(BaseModel):
    summary: str = Field(min_length=10, max_length=1500)
    open_questions: list[str] = Field(default_factory=list, max_length=8)


@dataclass
class InvestigationContext:
    """What the tools share during one investigation."""

    paypal_record: str
    sink: EvidenceSink
    recorded_quotes: dict[str, int] = field(default_factory=dict)
    recorded_kinds: list[str] = field(default_factory=list)
    finish_attempts: int = 0
    finished: bool = False


def brand_for_host(host: str) -> str:
    for site in WORLD["sites"].values():
        if site["host"] == host:
            return site["brand"]
    return host


def build_investigator_tools(browser: BrowserSession, context: InvestigationContext) -> list[BaseTool]:
    @tool(args_schema=EvidenceInput)
    async def record_evidence(
        kind: EvidenceKind,
        claim: str,
        quote: str,
        supports: Supports = "neutral",
        source: Literal["page", "paypal"] = "page",
    ) -> str:
        """Record one fact you rely on. Call it while the page that shows the fact is on screen.

        `quote` must be copied word for word from the current page (or from the PayPal record when `source` is
        "paypal"). The tool rejects quotes that are not there, so nothing can be invented.
        """
        if source == "paypal":
            haystack, label, url, screenshot = context.paypal_record, "PayPal", "", None
        else:
            page_text = await browser.get_page_text()
            form_values = await browser.get_form_values()
            haystack = f"{page_text}\n" + "\n".join(form_values)
            url = browser.page.url
            label = brand_for_host(urlsplit(url).hostname or "")
        problem = check_quote(quote, haystack)
        if problem:
            return problem
        key = normalize(quote)
        if key in context.recorded_quotes:
            return f"Already recorded as E{context.recorded_quotes[key]}. Record a different fact or move on."
        if source != "paypal":
            screenshot = await browser.capture_screen_frame(SCREENSHOT_QUALITY)
        evidence_id = await context.sink.add_evidence(
            EvidenceItem(
                kind=kind,
                claim=claim.strip(),
                quote=quote.strip(),
                supports=supports,
                source_label=label,
                source_url=url,
                screenshot=screenshot,
            )
        )
        context.recorded_quotes[key] = evidence_id
        context.recorded_kinds.append(kind)
        return f"Recorded E{evidence_id} ({len(context.recorded_quotes)} facts so far)."

    @tool(args_schema=FinishInput)
    async def finish_investigation(summary: str, open_questions: list[str] | None = None) -> str:
        """Finish the investigation once every tool is covered. The summary states what the evidence shows and
        never recommends an action."""
        context.finish_attempts += 1
        missing = [kind for kind in REQUIRED_KINDS if kind not in context.recorded_kinds]
        if missing and context.finish_attempts == 1:
            return (
                f"Not finished: no evidence recorded yet for {', '.join(missing)}. Visit the remaining tools and "
                "record those facts, or call finish_investigation again if they really are not available and "
                "explain the gap in open_questions."
            )
        await context.sink.finish(summary.strip(), [question.strip() for question in open_questions or []])
        context.finished = True
        return "Investigation recorded. Reply with one short line and stop."

    return [record_evidence, finish_investigation]
