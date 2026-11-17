"""Structured JSON logging to stdout (-> CloudWatch Logs).

Every line is one JSON object: {"ts", "level", "logger", "msg", "request_id", "user_id", ...}.
Use `bind(...)` to attach context to all subsequent lines of the current request and
`logger.info("msg", extra={"data": {...}})` (or the `log_event` helper) for structured fields.
Never log passwords, session tokens or API keys.
"""
import contextvars
import json
import logging
import sys
import time

from .config import settings

_context: contextvars.ContextVar[dict] = contextvars.ContextVar("log_context", default={})


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            **_context.get(),
        }
        data = getattr(record, "data", None)
        if isinstance(data, dict):
            entry.update(data)
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


_configured = False


def setup() -> None:
    global _configured
    if _configured:
        return
    root = logging.getLogger()
    for h in list(root.handlers):  # the Lambda runtime installs its own plain-text handler
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(settings.log_level)
    for noisy in ("botocore", "boto3", "urllib3", "s3transfer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    setup()
    return logging.getLogger(name)


def bind(**fields) -> None:
    _context.set({**_context.get(), **{k: v for k, v in fields.items() if v is not None}})


def clear() -> None:
    _context.set({})


def log_event(logger: logging.Logger, level: int, msg: str, **data) -> None:
    logger.log(level, msg, extra={"data": data})
