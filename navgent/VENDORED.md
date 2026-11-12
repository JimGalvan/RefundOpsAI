# navgent: browser-agent core

The browser-agent core used by RefundOps: a browser agent built on LangChain deepagents and Playwright, under the repository's MIT license.


## Not included

- `navgent/evidence/`: a bug-report evidence pipeline (needs OpenCV; RefundOps does not use it).
- The `google-maps-place-research` skill. `browser_agent/skills/` stays (empty) because the agent loads skills from that folder.
- All `__pycache__` folders.

## RefundOps-specific changes

Everything RefundOps-specific lives outside this folder, in `investigator/`. Any edit made inside `navgent/` is listed here with the reason:

- `session.py`: `BrowserAgentSession` takes an optional `agent_builder` argument (default: `build_browser_agent`) so RefundOps can plug in its investigator agent without copying the session.
