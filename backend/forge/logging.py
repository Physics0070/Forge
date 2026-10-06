"""Structured JSON logging with secret redaction."""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone

_SECRET_KEYS = re.compile(r"(key|secret|password|token|authorization|cookie)", re.I)
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.I)


def redact(value):
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if _SECRET_KEYS.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _BEARER.sub(r"\1[REDACTED]", value)
    return value


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "service": self.service,
            "event": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if extra:
            payload.update(redact(extra))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(service: str, level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def log(event: str, level: int = logging.INFO, **fields) -> None:
    logging.getLogger("forge").log(level, event, extra={"fields": fields})
