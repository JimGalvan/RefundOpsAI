"""Builds the investigator agent: Navgent's browser tools (minus the bug verdict) plus the evidence tools."""

from collections.abc import Callable
from typing import Any

from deepagents import create_deep_agent
from langchain.agents.middleware import ModelRetryMiddleware, TodoListMiddleware
from langgraph.checkpoint.memory import InMemorySaver

from investigator.prompts import SYSTEM_PROMPT, TODO_SYSTEM_PROMPT
from investigator.tools import InvestigationContext, build_investigator_tools
from navgent.browser.browser_session import BrowserSession
from navgent.browser_agent.context import AgentContext
from navgent.browser_agent.tools import build_browser_tools
from navgent.credentials import CredentialProvider
from navgent.page_navigator.page_navigator import build_page_navigator
from navgent.redaction import SecretRedactor
from navgent.sensitive_input import SensitiveInputChannel
from navgent.settings import AgentSettings
from navgent.shared.llm import build_strong_model


def make_agent_builder(context: InvestigationContext) -> Callable[..., Any]:
    """An `agent_builder` for `navgent.BrowserAgentSession` bound to one investigation's context."""

    def build(
        browser: BrowserSession,
        sensitive_inputs: SensitiveInputChannel,
        settings: AgentSettings,
        credential_provider: CredentialProvider | None = None,
        redactor: SecretRedactor | None = None,
    ) -> Any:
        browser_tools = build_browser_tools(
            browser,
            credential_provider=credential_provider,
            sensitive_inputs=sensitive_inputs,
            credential_host=settings.credential_host,
            redactor=redactor,
        )
        # The agent records facts; it never reports a verdict.
        tools = [tool for tool in browser_tools if tool.name != "report_verdict"]
        tools += build_investigator_tools(browser, context)
        return create_deep_agent(
            model=build_strong_model(settings.strong_model),
            checkpointer=InMemorySaver(),
            system_prompt=SYSTEM_PROMPT,
            tools=tools,
            subagents=[build_page_navigator(browser, settings)],
            middleware=[
                ModelRetryMiddleware(max_retries=3, backoff_factor=2.0, initial_delay=1.0),
                TodoListMiddleware(system_prompt=TODO_SYSTEM_PROMPT),
            ],
            context_schema=AgentContext,
        )

    return build
