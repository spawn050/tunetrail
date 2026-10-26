"""Structured logging that redacts secrets before output."""

import json
import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "client_secret",
        "credentials",
        "password",
        "refresh_token",
        "token",
    }
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_ASSIGNMENT_SECRET = re.compile(
    r"(?i)\b(access_token|api_key|authorization|client_secret|password|refresh_token|token)"
    r"(\s*[:=]\s*)([^,\s\]}]+)"
)


def redact(value: Any) -> Any:
    """Recursively remove secret values from structured logging data."""
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        return {
            str(key): "[REDACTED]" if str(key).lower() in _SENSITIVE_KEYS else redact(item)
            for key, item in mapping.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        sequence = cast(list[object] | tuple[object, ...] | set[object] | frozenset[object], value)
        return [redact(item) for item in sequence]
    if isinstance(value, str):
        bearer_redacted = _BEARER_TOKEN.sub("Bearer [REDACTED]", value)
        return _ASSIGNMENT_SECRET.sub(r"\1\2[REDACTED]", bearer_redacted)
    return value


class RedactingJsonFormatter(logging.Formatter):
    """Emit JSON records after redacting messages and `context` extras."""

    def format(self, record: logging.LogRecord) -> str:
        """Format an individual log record as a redacted JSON object."""
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        context = record.__dict__.get("context")
        if context is not None:
            payload["context"] = redact(context)
        return json.dumps(payload, default=str, sort_keys=True)


def configure_logging(level: str = "INFO") -> None:
    """Configure TuneTrail's root logger with a single redacting JSON handler."""
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingJsonFormatter())
    logger = logging.getLogger("tunetrail")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
