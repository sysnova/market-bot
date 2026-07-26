"""Central structlog configuration and request-context helpers."""

import logging
import sys
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars
from structlog.typing import FilteringBoundLogger


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    """Configure structlog and the standard library from one log-level setting."""
    numeric_level = logging.getLevelNamesMapping().get(level.upper())
    if numeric_level is None:
        msg = f"unknown log level: {level}"
        raise ValueError(msg)

    logging.basicConfig(level=numeric_level, stream=sys.stdout, force=True)
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )


def bind_context(**values: Any) -> None:  # noqa: ANN401
    """Bind correlation fields to the current async/thread context."""
    bind_contextvars(**values)


def clear_context() -> None:
    """Clear all correlation fields at an execution boundary."""
    clear_contextvars()


def get_logger(name: str | None = None) -> FilteringBoundLogger:
    """Return a configured structured logger."""
    return structlog.get_logger(name)
