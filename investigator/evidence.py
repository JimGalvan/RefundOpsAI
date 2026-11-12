"""Quote verification and the evidence record the agent produces."""

from dataclasses import dataclass
from typing import Literal, Protocol

EvidenceKind = Literal["payment", "dispute", "fulfillment", "tracking", "policy", "message", "other"]
Supports = Literal["buyer", "merchant", "neutral", "untrusted"]

_TYPOGRAPHY = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        " ": " ",
        "–": "-",
        "—": "-",
    }
)

# Evidence kinds a finished investigation should cover (see finish_investigation).
REQUIRED_KINDS = ("payment", "fulfillment", "tracking", "policy")


def normalize(text: str) -> str:
    """Collapse whitespace and straighten typographic quotes and dashes so copy-paste differences don't matter."""
    return " ".join(text.translate(_TYPOGRAPHY).split())


def check_quote(quote: str, haystack: str) -> str | None:
    """None when the quote appears in the haystack, otherwise a message the agent can act on."""
    wanted = normalize(quote)
    if not wanted:
        return "The quote is empty. Copy a short line from the page word for word."
    if wanted in normalize(haystack):
        return None
    return (
        "That quote is not on the current page. Open the page that shows the fact, call read_page_text if you need the "
        "whole page, and copy the quote word for word (one line or one sentence), then call record_evidence again."
    )


@dataclass(frozen=True)
class EvidenceItem:
    kind: str
    claim: str
    quote: str
    supports: str
    source_label: str
    source_url: str
    screenshot: bytes | None = None


class EvidenceSink(Protocol):
    """Where recorded evidence goes. The runner implements this with the database and the live view."""

    async def add_evidence(self, item: EvidenceItem) -> int: ...

    async def finish(self, summary: str, open_questions: list[str]) -> None: ...
