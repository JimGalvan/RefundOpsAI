import asyncio
import json
import logging
from uuid import uuid4

from langchain_core.tools import BaseTool, tool
from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt
from pydantic import ValidationError

from navgent.browser.browser_session import BrowserSession
from navgent.browser_agent.constants import (
    ACTION_DELAY_SECONDS,
    PAGE_TEXT_CHARACTER_LIMIT,
)
from navgent.browser_agent.findings import finding_payloads
from navgent.browser_agent.schema_utils import SchemaUtils
from navgent.browser_agent.steps import (
    BrowserStep,
    ClickStep,
    CredentialFillStep,
    FillStep,
    MfaCodeStep,
    PressStep,
    SelectStep,
    StepPlan,
    TypeStep,
    changes_fields,
    check_brief_order,
    step_event_payload,
    step_reference,
    validate_plan,
)
from navgent.browser_agent.verdict import VerdictReport, check_observed
from navgent.credentials import CredentialProvider
from navgent.models import Verdict
from navgent.redaction import SecretRedactor
from navgent.sensitive_input import SensitiveInputChannel
from navgent.shared.events import publish_custom_event
from navgent.shared.verification import describe_entered_code, explain_missing_code

STEP_PLAN_SCHEMA = SchemaUtils.inline_references(StepPlan.model_json_schema())

logger = logging.getLogger(__name__)


def build_browser_tools(
        browser: BrowserSession,
        credential_provider: CredentialProvider | None = None,
        sensitive_inputs: SensitiveInputChannel | None = None,
        credential_host: str | None = None,
        redactor: SecretRedactor | None = None,
) -> list[BaseTool]:
    sensitive_inputs = sensitive_inputs or SensitiveInputChannel()
    redactor = redactor or SecretRedactor()
    browser_lock = asyncio.Lock()
    last_brief_step = 0

    @tool
    async def navigate_and_observe(url: str) -> str:
        """Navigate to a URL and return the resulting page snapshot."""
        async with browser_lock:
            publish_custom_event("browser.navigation.started", {"url": url})
            observation = await browser.attempt_navigate_and_observe(url)
            publish_custom_event("browser.navigation.completed", {"url": url})
            return str(observation)

    @tool
    async def observe_page() -> str:
        """Return the current page snapshot without navigating."""
        async with browser_lock:
            observation = await browser.attempt_observe_page()
            publish_custom_event("browser.observation", {"message": "Observed the current page"})
            return str(observation)

    @tool
    async def read_page_text() -> str:
        """Return the full text of the current page or selected frame, for reading values an observation shortened."""
        async with browser_lock:
            page_text = await browser.get_page_text()
        if len(page_text) <= PAGE_TEXT_CHARACTER_LIMIT:
            return page_text
        omitted = len(page_text) - PAGE_TEXT_CHARACTER_LIMIT
        return f"{page_text[:PAGE_TEXT_CHARACTER_LIMIT]}…[{omitted} chars omitted]"

    @tool(args_schema=VerdictReport)
    async def report_verdict(
            verdict: Verdict, expected: str, observed: str, reason: str, step_number: int | None = None,
    ) -> str:
        """Report whether the bug reproduced. Call once, after checking the assertion, while the value is on screen.

        `observed` quotes page text, or a form field as `Label: value` with `(empty)` for a blank field.
        Separate several quotes with `; `, such as `First Name: r; Last Name: (empty)`.
        For `reproduced`, each quote that appears once on screen is outlined in the recording.
        """
        if verdict != "inconclusive":
            async with browser_lock:
                page_text = await browser.get_page_text()
                form_values = await browser.get_form_values()
            problem = check_observed(observed, page_text + "\n" + "\n".join(form_values))
            if problem:
                return problem
        verdict_id = uuid4().hex
        publish_custom_event("reproduction.verdict", {
            "verdict": verdict,
            "expected": expected,
            "observed": observed,
            "step_number": step_number,
            "reason": reason,
            "verdict_id": verdict_id,
        })
        if verdict == "reproduced":
            await publish_findings(verdict_id, observed, expected, step_number)
        return "Verdict recorded. Finish with a one-line summary."

    @tool
    async def switch_tab(tab_id: str) -> str:
        """Switch to the tab identified by a value such as `tab:1`."""
        async with browser_lock:
            result = await browser.switch_tab(tab_id)
            publish_custom_event("browser.tab.changed", {"tab_id": tab_id})
            return result

    @tool
    async def switch_frame(frame_id: str) -> str:
        """Switch to a frame by an id such as `frame:1` from <available-iframes>; `frame:0` is the main page."""
        async with browser_lock:
            result = await browser.switch_to_iframe(frame_id)
            publish_custom_event("browser.frame.changed", {"frame_id": frame_id})
            return result

    @tool
    def request_user_input(question: str) -> str:
        """Pause and ask the user for required non-sensitive information."""
        publish_custom_event("input.required", {"question": question})
        answer = interrupt({"kind": "question", "question": question})
        return str(answer)

    @tool
    async def list_selected_credentials() -> str:
        """List safe metadata for credentials selected for this run."""
        if not credential_provider:
            return "[]"

        described = []
        for item in await credential_provider.list_selected():
            described.append(
                {
                    "id": str(item.id),
                    "name": item.name,
                    "allowed_domains": item.allowed_domains,
                    "available_fields": item.available_fields,
                    "preferred_verification": item.preferred_verification,
                }
            )
        return json.dumps(described)

    @tool(args_schema=STEP_PLAN_SCHEMA)
    async def execute_steps(steps: list, reason: str = "", brief_step: int | None = None) -> str:
        """Validate and execute browser steps, including credential and MFA steps."""
        nonlocal last_brief_step
        async with browser_lock:
            try:
                plan = StepPlan.model_validate({"steps": steps, "reason": reason, "brief_step": brief_step})
                check_brief_order(plan.brief_step, last_brief_step)
                validate_plan(browser, plan.steps)
            except (ValidationError, ValueError) as exception:
                return json.dumps(
                    {
                        "ok": False,
                        "kind": "validation_error",
                        "completed": [],
                        "failed": None,
                        "error": str(exception),
                    }
                )

            last_brief_step = plan.brief_step
            browser.current_brief_step = plan.brief_step
            previous_url = browser.page.url
            completed = []
            notes = []
            for step in plan.steps:
                reference = step_reference(step)
                event_payload = step_event_payload(browser, step, plan)
                publish_custom_event("browser.action.started", event_payload)
                try:
                    note = await run_step(step)
                except GraphInterrupt:
                    raise
                except Exception as exception:
                    error = redactor.redact(str(exception))
                    publish_clicks(step, plan)
                    publish_custom_event("browser.action.failed", {**event_payload, "error": error})
                    return json.dumps(
                        {
                            "ok": False,
                            "kind": "execution_error",
                            "completed": completed,
                            "failed": reference,
                            "error": error,
                            "notes": notes,
                        }
                    )

                publish_clicks(step, plan)
                if note is not None:
                    notes.append(note)
                completed.append(reference)
                completed_payload = dict(event_payload)
                if changes_fields(step):
                    completed_payload["fields"] = await read_form_values()
                publish_custom_event("browser.action.completed", completed_payload)
                await asyncio.sleep(ACTION_DELAY_SECONDS)

            current_url = await browser.wait_for_page_to_settle()
            if current_url != previous_url:
                notes.append(redactor.redact(f"URL changed to {current_url}"))

            return json.dumps(
                {
                    "ok": True,
                    "kind": "success",
                    "completed": completed,
                    "failed": None,
                    "notes": notes,
                }
            )

    async def publish_findings(verdict_id: str, observed: str, expected: str, step_number: int | None) -> None:
        try:
            async with browser_lock:
                payloads = await finding_payloads(browser, verdict_id, observed, expected, step_number)
        except Exception:
            logger.warning("Could not locate the finding on screen", exc_info=True)
            return
        for payload in payloads:
            publish_custom_event("reproduction.finding", payload)

    def publish_clicks(step: BrowserStep, plan: StepPlan) -> None:
        for click in browser.take_performed_clicks():
            publish_custom_event("browser.click", click.event_payload(step.action, plan.brief_step))

    async def read_form_values() -> list[str]:
        try:
            return await browser.get_form_values()
        except Exception:
            logger.warning("Could not read form values after a step", exc_info=True)
            return []

    async def run_step(step: BrowserStep) -> str | None:
        if isinstance(step, ClickStep):
            await browser.click(step.element_index)
        elif isinstance(step, FillStep):
            await browser.fill(step.element_index, step.value)
        elif isinstance(step, TypeStep):
            await browser.type_text(step.element_index, step.value)
        elif isinstance(step, SelectStep):
            await browser.select(step.element_index, step.value)
        elif isinstance(step, PressStep):
            await browser.press(step.element_index, step.value)
        elif isinstance(step, CredentialFillStep):
            await fill_credential(step)
        elif isinstance(step, MfaCodeStep):
            return await request_mfa_code(step)
        return None

    async def fill_credential(step: CredentialFillStep) -> None:
        if not credential_provider:
            raise RuntimeError("Credential storage is not configured")

        async def read_value(hostname: str) -> str:
            secret = await credential_provider.resolve(step.credential_id, hostname)
            value = getattr(secret, step.field)
            del secret
            if not value:
                raise ValueError(f"The selected credential has no saved {step.field} field")
            redactor.add(value)
            return value

        target_host = credential_host or browser.current_hostname
        await browser.fill_credential(step.element_index, target_host, read_value)

    async def request_mfa_code(step: MfaCodeStep) -> str:
        request = sensitive_inputs.begin(step.element_index, step.prompt, step.submit_element_index)
        publish_custom_event(
            "sensitive_input.required",
            {
                "request_id": str(request.id),
                "kind": request.kind,
                "prompt": request.prompt,
            },
        )
        result = interrupt({"kind": request.kind, "request_id": str(request.id)})
        sensitive_inputs.clear(request.id)

        if not isinstance(result, dict) or result.get("entered") is not True:
            raise RuntimeError(explain_missing_code(result))
        return describe_entered_code(result)

    return [
        navigate_and_observe,
        observe_page,
        read_page_text,
        report_verdict,
        execute_steps,
        list_selected_credentials,
        switch_tab,
        switch_frame,
        request_user_input,
    ]
