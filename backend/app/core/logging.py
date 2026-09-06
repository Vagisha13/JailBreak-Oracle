import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict

from app.core.config import settings

# Context fields attached via ``extra=`` that are safe to include in JSON output.
_SAFE_CONTEXT_FIELDS = (
    "event_name",
    "user_id",
    "campaign_id",
    "experiment_id",
    "attack_id",
    "vulnerability_id",
    "strategy",
    "iteration",
    "status",
    "provider",
    "model",
    "role",
    "attempt",
    "error_type",
    "error_id",
    "error_message",
    "latency_ms",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "transient",
    "path",
    "queue",
    "num_attacks",
)


class JsonFormatter(logging.Formatter):
    """JSON-lines formatter for structured production logging."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for field in _SAFE_CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class MaskingFormatter(JsonFormatter):
    """Formatter that redacts known secret constructs before emitting logs.

    A single compiled regex masks each complete secret (``key=value``,
    ``Bearer <token>``, ``sk-...``, JWT ``eyJ...`` headers) in one pass, so a
    redacted value can never re-trigger a sibling rule (e.g. ``x-api-key``
    would otherwise re-match ``api-key`` inside its own replacement output).
    """

    # Each alternative consumes the *full* construct it detects. Key names are
    # matched case-insensitively; values stop at any whitespace/quote so JSON
    # payloads and query-style strings are both handled.
    _SECRET_PATTERN_RE = re.compile(
        r"(?i)"
        r"((?:api[_-]?key|x-api-key|client[_-]?secret|access_token|refresh_token|"
        r"auth_token|authorization|password|private[_-]?key|secret|bearer_token))"
        r"\s*[=:]\s*[\"']?[^\s\"',;]+"
        r"|(Bearer\s+[A-Za-z0-9._/\-+]+)"
        r"|(sk-[A-Za-z0-9_\-]+)"
        r"|(eyJ[A-Za-z0-9_\-]{8,})"
    )

    def _redact(self, match: re.Match) -> str:
        if match.group(1):
            return f"{match.group(1)}=***"
        if match.group(2):
            return "Bearer ***"
        if match.group(3):
            return "sk-***"
        return "eyJ***"

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        return self._SECRET_PATTERN_RE.sub(self._redact, formatted)


_configured_loggers: Dict[str, logging.Logger] = {}

_HANDLER = None


def _get_handler() -> logging.Handler:
    global _HANDLER
    if _HANDLER is None:
        _HANDLER = logging.StreamHandler(sys.stdout)
        _HANDLER.setFormatter(MaskingFormatter())
    return _HANDLER


def get_logger(name: str) -> logging.Logger:
    """Return a structured JSON logger for the given module name."""
    if name in _configured_loggers:
        return _configured_loggers[name]

    logger = logging.getLogger(name)
    handler = _get_handler()
    if handler not in logger.handlers:
        logger.addHandler(handler)
    logger.setLevel(settings.LOG_LEVEL.upper())
    logger.propagate = False
    _configured_loggers[name] = logger
    return logger
