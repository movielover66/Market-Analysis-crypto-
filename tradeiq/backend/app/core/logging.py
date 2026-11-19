"""
Structured JSON logging with mandatory secret scrubbing and request ID correlation.
"""
import contextvars
import datetime
import json
import logging
import re
import sys
from typing import Any

request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_SENSITIVE_KEY_RE = re.compile(
    r"(password|pin|secret|token|api_key|apikey|anon_key|service_role|authorization|jwt|totp|dsn)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE)


def scrub_secrets(value: Any) -> Any:
    """Recursively scrubs sensitive keys and Bearer tokens from dicts, lists, and strings."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if _SENSITIVE_KEY_RE.search(str(k)):
                out[k] = "[REDACTED]"
            else:
                out[k] = scrub_secrets(v)
        return out
    if isinstance(value, (list, tuple)):
        return [scrub_secrets(item) for item in value]
    if isinstance(value, str):
        return _BEARER_RE.sub(r"\1[REDACTED]", value)
    return value


class StructuredJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": request_id_ctx.get("-"),
            "message": scrub_secrets(record.getMessage()),
        }
        extra_data = getattr(record, "structured_data", None)
        if extra_data is not None:
            payload["data"] = scrub_secrets(extra_data)
        if record.exc_info:
            payload["exc_info"] = scrub_secrets(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("tradeiq")
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredJsonFormatter())
        logger.addHandler(handler)
    logger.propagate = False
    return logger
