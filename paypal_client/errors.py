"""PayPal errors with safe, concise display text."""

import re
from typing import Any


def safe_text(value: object, secrets: tuple[str, ...] = ()) -> str:
    text = str(value)
    for secret in sorted(filter(None, secrets), key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)\b(?:Bearer|Basic)\s+\S+", "[REDACTED]", text)
    text = re.sub(r"(?i)Authorization\s*:[^\r\n]*", "[REDACTED]", text)
    return " ".join(text.split())


class PayPalError(Exception):
    """An unsuccessful REST response; raw preserves the parsed body or text."""

    def __init__(
        self,
        status: int,
        name: str,
        message: str,
        debug_id: str | None = None,
        details: list[dict[str, Any]] | None = None,
        raw: Any = None,
        *,
        secrets: tuple[str, ...] = (),
    ) -> None:
        self.status = status
        self.name = name
        self.message = message
        self.debug_id = debug_id
        self.details = details if details is not None else []
        self.raw = raw
        safe_name = safe_text(name, secrets)
        safe_message = safe_text(message, secrets)
        safe_debug_id = safe_text(debug_id or "-", secrets)
        super().__init__(
            f"PayPal {status} {safe_name}: {safe_message} (debug_id={safe_debug_id})"
        )
