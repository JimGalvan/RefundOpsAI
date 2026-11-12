# Vendored Navgent core

This package is copied from the author's private project **Navgent**, a browser agent built on LangChain deepagents and Playwright. Navgent was written for a different hackathon. It is reused here under the MIT license (see the repository `LICENSE`, same copyright holder).

- **Source commit:** `821b217b10b99cd5061d7d6a9da01dfec25c517a` (no uncommitted changes under `navgent/` at the time)
- **Vendored on:** 2026-11-11
- **Why vendored, not installed:** the Navgent repository is private, and this repository has to run on its own.

## Removed at vendoring time

- `navgent/evidence/`: Navgent's bug-report evidence pipeline (needs OpenCV; RefundOps does not use it).
- The `google-maps-place-research` skill. `browser_agent/skills/` stays (empty) because the agent loads skills from that folder.
- All `__pycache__` folders.

## Changes since vendoring

Everything RefundOps-specific lives outside this folder, in `investigator/`. Any edit made inside `navgent/` is listed here with the reason:

- `session.py`: `BrowserAgentSession` takes an optional `agent_builder` argument (default: Navgent's own `build_browser_agent`) so RefundOps can plug in its investigator agent without copying the session.
