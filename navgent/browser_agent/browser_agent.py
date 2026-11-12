from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend
from langchain.agents.middleware import ModelRetryMiddleware, TodoListMiddleware
from langgraph.checkpoint.memory import InMemorySaver

from navgent.browser.browser_session import BrowserSession
from navgent.browser_agent.context import AgentContext
from navgent.browser_agent.prompts import SYSTEM_PROMPT, TODO_SYSTEM_PROMPT
from navgent.browser_agent.tools import build_browser_tools
from navgent.credentials import CredentialProvider
from navgent.page_navigator.page_navigator import build_page_navigator
from navgent.redaction import SecretRedactor
from navgent.sensitive_input import SensitiveInputChannel
from navgent.settings import AgentSettings
from navgent.shared.llm import build_strong_model


def build_browser_agent(
        browser_session: BrowserSession,
        sensitive_inputs: SensitiveInputChannel,
        settings: AgentSettings,
        credential_provider: CredentialProvider | None = None,
        redactor: SecretRedactor | None = None,
):
    agent_skills_path = Path(__file__).resolve().parent / "skills"
    agent_files_path = Path(__file__).resolve().parent / "files"

    tools = build_browser_tools(
        browser_session,
        credential_provider=credential_provider,
        sensitive_inputs=sensitive_inputs,
        credential_host=settings.credential_host,
        redactor=redactor,
    )
    page_navigator_subagent = build_page_navigator(browser_session, settings)

    workspace_backend = FilesystemBackend(
        root_dir=agent_files_path,
        virtual_mode=True,
    )
    skills_backend = FilesystemBackend(
        root_dir=agent_skills_path,
        virtual_mode=True,
    )
    backend = CompositeBackend(
        default=workspace_backend,
        routes={
            "/skills/": skills_backend,
        },
    )

    agent = create_deep_agent(
        model=build_strong_model(settings.strong_model),
        backend=backend,
        checkpointer=InMemorySaver(),
        system_prompt=SYSTEM_PROMPT,
        skills=["/skills"],
        tools=tools,
        subagents=[page_navigator_subagent],
        middleware=[
            ModelRetryMiddleware(
                max_retries=3,
                backoff_factor=2.0,
                initial_delay=1.0,
            ),
            TodoListMiddleware(system_prompt=TODO_SYSTEM_PROMPT),
        ],
        context_schema=AgentContext,
    )
    return agent
