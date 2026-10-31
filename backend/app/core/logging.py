"""Logging configuration.

Two output formats are supported:

* ``text`` — the human-friendly format used by local development.
* ``json`` — one JSON object per line, keyed by ``ts``/``level``/``logger``/
  ``message`` plus every ``extra=`` field, which is what Loki/ELK/CloudWatch
  expect. Request-scoped fields (``request_id``, ``trace_id``) are injected by
  ``RequestContextFilter`` so any log line emitted while serving a request can
  be joined back to the HTTP call and the trace.
"""

import json
import logging
import time
from contextvars import ContextVar
from logging.config import dictConfig
from typing import Any

request_id_var: ContextVar[str] = ContextVar("request_id", default="")
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")

_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
        "request_id",
        "trace_id",
    }
)


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", ""):
            record.request_id = request_id_var.get()
        if not getattr(record, "trace_id", ""):
            record.trace_id = trace_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            if isinstance(value, str | int | float | bool) or value is None:
                payload[key] = value
            else:
                payload[key] = str(value)
        if record.request_id:
            payload["request_id"] = record.request_id
        if record.trace_id:
            payload["trace_id"] = record.trace_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO", log_format: str = "text") -> None:
    use_json = log_format.lower() == "json"
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {"request_context": {"()": RequestContextFilter}},
            "formatters": {
                "default": {
                    "format": "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                },
                "json": {"()": JsonFormatter},
                "access": {
                    "format": (
                        "%(asctime)s | %(levelname)-8s | %(name)s | "
                        "request_id=%(request_id)s %(message)s"
                    )
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "json" if use_json else "default",
                    "filters": ["request_context"],
                }
            },
            "root": {
                "handlers": ["console"],
                "level": level.upper(),
            },
        }
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
