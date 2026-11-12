"""Model and browser settings for the investigator, read from the environment."""

import os
from pathlib import Path

from django.conf import settings

from navgent.browser.constants import ASSET_HOSTS
from navgent.settings import AgentSettings

# Cheapest reliable first (docs/PLAN.md section 4.6). Both models run on Vultr Serverless Inference.
DEFAULT_STRONG_MODEL = "vultr:glm-5.3"
DEFAULT_FAST_MODEL = "vultr:glm-5.3-flash"


def model_names() -> tuple[str, str, str]:
    """Strong (agent loop and recommendation), fast (page reads), evidence model names."""
    strong = os.getenv("REFUNDOPS_STRONG_MODEL") or DEFAULT_STRONG_MODEL
    fast = os.getenv("REFUNDOPS_FAST_MODEL") or DEFAULT_FAST_MODEL
    evidence = os.getenv("REFUNDOPS_EVIDENCE_MODEL") or fast
    return strong, fast, evidence


def agent_settings(*, recording_dir: Path | None = None) -> AgentSettings:
    strong, fast, evidence = model_names()
    return AgentSettings(
        strong_model=strong,
        fast_model=fast,
        evidence_model=evidence,
        headless=os.getenv("REFUNDOPS_HEADLESS", "1") != "0",
        allowed_hosts=tuple(settings.SITE_HOSTS),
        asset_hosts=tuple(ASSET_HOSTS),
        max_runtime_seconds=float(os.getenv("REFUNDOPS_MAX_RUNTIME_SECONDS", "300")),
        recording_dir=recording_dir,
    )
