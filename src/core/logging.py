"""Structured logging (structlog on top of stdlib logging).

Every log line carries the context bound via ``bind_context`` (e.g. ``request_id``), so a single
HTTP request or task can be followed across components. JSON in prod, readable console in dev.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

from src.core.config import LoggingSettings

_REDACT_KEYS = frozenset({"password", "token", "secret", "authorization", "cookie", "api_key"})
_REDACT_SUFFIXES = tuple(f"_{k}" for k in _REDACT_KEYS)


def _redact(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Defence in depth: never emit values under obviously sensitive keys."""
    for key in list(event_dict):
        lowered = key.lower()
        if lowered in _REDACT_KEYS or lowered.endswith(_REDACT_SUFFIXES):
            event_dict[key] = "***"
    return event_dict


class _CurrentStderrHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Writes to whatever `sys.stderr` is at emit time, not the object present at configuration.

    Servers and test runners may replace `sys.stderr`; a handler pinned to the old object would
    silently write to a stream nobody reads.
    """

    @property
    def stream(self) -> Any:
        return sys.stderr

    @stream.setter
    def stream(self, _value: Any) -> None:
        pass


def configure_logging(settings: LoggingSettings) -> None:
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact,
    ]
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if settings.format == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )
    handler = _CurrentStderrHandler()
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.level)
    # uvicorn installs its own handlers; route them through ours.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)  # we log requests ourselves


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger


bind_context = structlog.contextvars.bind_contextvars
clear_context = structlog.contextvars.clear_contextvars
