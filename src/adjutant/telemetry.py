"""Structured, allowlisted request telemetry that excludes bodies and credentials."""

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime

import httpx

trace_id: ContextVar[str] = ContextVar("adjutant_trace_id", default="")


class CredentialQueryFilter(logging.Filter):
    """OAuth callback codes and provider query credentials must never reach access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            args = list(record.args)
            for index, value in enumerate(args):
                if isinstance(value, httpx.URL):
                    args[index] = value.copy_with(query=None)
                elif isinstance(value, str) and (
                    value.startswith(("https://", "http://"))
                    or (record.name == "uvicorn.access" and index == 2)
                ):
                    args[index] = value.split("?", 1)[0]
            record.args = tuple(args)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        item = {
            "time": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "trace_id": getattr(record, "trace_id", trace_id.get()),
        }
        for field in (
            "method",
            "route",
            "status",
            "elapsed_ms",
            "workflow_id",
            "error_type",
        ):
            if hasattr(record, field):
                item[field] = getattr(record, field)
        return json.dumps(item, ensure_ascii=False)


def configure_logging() -> None:
    for name in ("httpx", "uvicorn.access"):
        external = logging.getLogger(name)
        if not any(
            isinstance(item, CredentialQueryFilter) for item in external.filters
        ):
            external.addFilter(CredentialQueryFilter())
    logger = logging.getLogger("adjutant")
    if not any(getattr(handler, "adjutant_json", False) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        setattr(handler, "adjutant_json", True)
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
