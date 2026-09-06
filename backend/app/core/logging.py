import json
import logging
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
    "attempt",
    "error_type",
    "error_id",
    "latency_ms",
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
    """Formatter that redacts known secret patterns before emitting logs."""

    _SECRET_PATTERNS = (
        ("sk-", "sk-***"),
        ("api_key", "api_key=***"),
        ("password", "password=***"),
    )

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        for needle, replacement in self._SECRET_PATTERNS:
            if needle in formatted:
                formatted = formatted.replace(needle, replacement)
        return formatted


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
