from typing import Any

from langchain_quickjs import CodeInterpreterMiddleware

from navgent.browser.browser_session import BrowserSession
from navgent.page_navigator.prompts import SYSTEM_PROMPT
from navgent.page_navigator.tools import build_page_navigator_tools
from navgent.settings import AgentSettings
from navgent.shared.llm import build_fast_model


def build_page_navigator(browser: BrowserSession, settings: AgentSettings) -> dict[str, Any]:
    return {
        "name": "page-navigator",
        "description": (
            "Locate an off-screen target by reading viewport text for meaning and context. "
            "Provide a natural-language description of the target and its identifying details. "
            "Returns whether it was found, supporting text, and the reason for stopping."
        ),
        "system_prompt": SYSTEM_PROMPT,
        "tools": build_page_navigator_tools(browser),
        "model": build_fast_model(settings.fast_model),
        "middleware": [
            CodeInterpreterMiddleware(ptc=["get_text_in_viewport", "scroll"])
        ],
    }
