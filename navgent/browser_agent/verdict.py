from pydantic import BaseModel, ConfigDict, Field

from navgent.models import Verdict


class VerdictReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    expected: str
    observed: str
    step_number: int | None = Field(default=None, ge=1)
    reason: str = Field(min_length=1, max_length=500)


def check_observed(observed: str, evidence_text: str) -> str | None:
    if not observed.strip():
        return "observed is empty. Quote the value from the page, or report inconclusive."
    evidence = normalize(evidence_text)
    if normalize(observed) in evidence:
        return None
    missing = []
    for quote in observed.split(";"):
        if quote.strip() and normalize(quote) not in evidence:
            missing.append(quote.strip())
    if not missing:
        return None
    return (
        f"Not on the current page: {missing}. Quote page text or a form field as `Label: value` word for word, "
        "separate several quotes with `; `, switch to the tab or frame that shows them, then call report_verdict again."
    )


def normalize(text: str) -> str:
    return " ".join(text.split())
