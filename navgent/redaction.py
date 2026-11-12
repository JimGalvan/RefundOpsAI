import dataclasses
import html
import logging
import re
import threading
import traceback
import weakref
from typing import Any
from urllib.parse import quote, quote_plus

REDACTED = "[redacted]"
SHORT_VALUE_LENGTH = 4

_active_redactors: "weakref.WeakSet[SecretRedactor]" = weakref.WeakSet()
_factory_lock = threading.Lock()
_factory_installed = False


def _install_log_redaction() -> None:
    """Redact remembered values from every log record this process creates, including tracebacks."""
    global _factory_installed
    with _factory_lock:
        if _factory_installed:
            return
        previous_factory = logging.getLogRecordFactory()

        def redacting_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            record = previous_factory(*args, **kwargs)
            redactors = list(_active_redactors)
            if not redactors:
                return record
            for redactor in redactors:
                _redact_record(record, redactor)
            return record

        logging.setLogRecordFactory(redacting_factory)
        _factory_installed = True


def _redact_record(record: logging.LogRecord, redactor: "SecretRedactor") -> None:
    """Change a record only where it holds a remembered value, keeping the shape of its args.

    Formatters such as uvicorn's access log unpack `record.args`, so the args stay a tuple or dict.
    """
    try:
        message = record.getMessage()
    except Exception:
        return
    if redactor.redact(message) != message:
        if isinstance(record.msg, str):
            record.msg = redactor.redact(record.msg)
        record.args = _redact_log_args(record.args, redactor)
        try:
            still_leaks = redactor.redact(record.getMessage()) != record.getMessage()
        except Exception:
            still_leaks = True
        if still_leaks:
            record.msg = redactor.redact(message)
            record.args = ()

    if record.exc_info:
        exception_text = "".join(traceback.format_exception(*record.exc_info)).rstrip("\n")
        redacted_text = redactor.redact(exception_text)
        if redacted_text != exception_text:
            record.exc_text = redacted_text
            record.exc_info = None
    if record.stack_info:
        record.stack_info = redactor.redact(record.stack_info)


def _redact_log_args(args: Any, redactor: "SecretRedactor") -> Any:
    if isinstance(args, dict):
        redacted = {}
        for key, value in args.items():
            redacted[key] = _redact_log_arg(value, redactor)
        return redacted
    if isinstance(args, tuple):
        redacted_args = []
        for value in args:
            redacted_args.append(_redact_log_arg(value, redactor))
        return tuple(redacted_args)
    return args


def _redact_log_arg(value: Any, redactor: "SecretRedactor") -> Any:
    if isinstance(value, str):
        return redactor.redact(value)
    text = str(value)
    redacted = redactor.redact(text)
    if redacted != text:
        return redacted
    return value


class SecretRedactor:
    """Remembers credential values typed during a run and strips them from text leaving the agent.

    Values stay in this process only. Short values are matched as whole words so a two-letter
    first name does not mangle unrelated text.
    """

    def __init__(self) -> None:
        self._values: set[str] = set()
        self._pattern: re.Pattern[str] | None = None

    def add(self, value: str) -> None:
        value = value.strip()
        if not value or value in self._values:
            return
        self._values.add(value)
        self._pattern = self._build_pattern()
        _active_redactors.add(self)
        _install_log_redaction()

    def _build_pattern(self) -> re.Pattern[str]:
        variants = set()
        for value in self._values:
            for variant in (value, quote(value, safe=""), quote_plus(value), html.escape(value)):
                variants.add(variant)

        alternatives = []
        for variant in sorted(variants, key=len, reverse=True):
            escaped = re.escape(variant)
            if len(variant) < SHORT_VALUE_LENGTH:
                escaped = rf"(?<!\w){escaped}(?!\w)"
            alternatives.append(escaped)
        return re.compile("|".join(alternatives), re.IGNORECASE)

    def redact(self, text: str) -> str:
        if self._pattern is None or not text:
            return text
        return self._pattern.sub(REDACTED, text)

    def redact_data(self, data: Any) -> Any:
        """Redact every string inside nested dicts, lists, tuples and frozen dataclasses."""
        if self._pattern is None:
            return data
        if isinstance(data, str):
            return self.redact(data)
        if isinstance(data, dict):
            redacted = {}
            for key, value in data.items():
                redacted[key] = self.redact_data(value)
            return redacted
        if isinstance(data, list):
            return [self.redact_data(item) for item in data]
        if isinstance(data, tuple):
            return tuple(self.redact_data(item) for item in data)
        if dataclasses.is_dataclass(data) and not isinstance(data, type):
            changes = {}
            for field in dataclasses.fields(data):
                if field.init:
                    changes[field.name] = self.redact_data(getattr(data, field.name))
            return dataclasses.replace(data, **changes)
        return data
