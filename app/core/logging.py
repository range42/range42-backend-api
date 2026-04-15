"""Structured logging for Range42 backend.

Replaces stdlib logging across the app. Context bound via
`structlog.contextvars` so subprocess spawns can inherit trace_id,
deployment_id, attempt_id without thread-local surprises.
"""
from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from typing import Iterator

import structlog


class _NamedPrintLogger(structlog.PrintLogger):
    """PrintLogger that remembers the name it was constructed with."""

    def __init__(self, name: str | None = None, file=None):
        super().__init__(file=file)
        self.name = name


class _NamedPrintLoggerFactory:
    """Factory that captures the name argument passed to structlog.get_logger()."""

    def __init__(self, file=None):
        self._file = file

    def __call__(self, *args):
        name = args[0] if args else None
        return _NamedPrintLogger(name=name, file=self._file)


def configure_logging(json_output: bool = True, level: str = "INFO") -> None:
    """Configure structlog + stdlib root handler.

    :param json_output: If True emit JSON lines; else key=value console renderer.
    :param level: Minimum log level name.
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    def _add_logger_name(logger, method_name, event_dict):
        name = getattr(logger, "name", None)
        if name is not None:
            event_dict.setdefault("logger", name)
        return event_dict

    shared = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        _add_logger_name,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        timestamper,
    ]
    renderer = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=shared + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=_NamedPrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


@contextmanager
def bind_context(**kwargs) -> Iterator[None]:
    """Bind correlation keys for the duration of the block."""
    tokens = structlog.contextvars.bind_contextvars(**kwargs)
    try:
        yield
    finally:
        structlog.contextvars.reset_contextvars(**tokens)
