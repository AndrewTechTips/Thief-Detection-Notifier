"""Structured logging: structlog for application code, with stdlib/uvicorn logs routed through
the same pipeline so every line shares one format (colored console in dev, JSON in prod)."""

import logging
import sys
from typing import TextIO

import structlog
from structlog.typing import EventDict, Processor

from vision_hub.core.config import Settings

# Uvicorn installs its own handlers; we strip them so records propagate to our root handler.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
_QUIET_LOGGERS = {
    # Request logging is done by our middleware (with request IDs), so uvicorn's is redundant.
    "uvicorn.access": logging.WARNING,
    "watchfiles.main": logging.WARNING,
    # HTTP clients log full URLs at INFO, which would leak tokens in query strings.
    "httpx": logging.WARNING,
    "httpx2": logging.WARNING,
    "httpcore": logging.WARNING,
}


def _drop_color_message(_: object, __: str, event_dict: EventDict) -> EventDict:
    """Uvicorn attaches an ANSI-colored duplicate of each message; it is noise in our output."""
    event_dict.pop("color_message", None)
    return event_dict


class _HubHandler(logging.StreamHandler[TextIO]):
    """Root handler owned by this module, so reconfiguring never touches foreign handlers."""

    def __init__(self, stream: TextIO | None = None) -> None:
        super().__init__(stream or sys.stdout)
        self._follow_stdout = stream is None

    def emit(self, record: logging.LogRecord) -> None:
        if self._follow_stdout:
            # Resolve at emit time so redirected stdout (e.g. test capture) is respected.
            self.stream = sys.stdout
        super().emit(record)


def _shared_processors() -> list[Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]


def configure_logging(settings: Settings, *, stream: TextIO | None = None) -> None:
    """Configure structlog and the stdlib root logger. Safe to call more than once."""
    shared = _shared_processors()

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: list[Processor] = (
        [structlog.processors.dict_tracebacks, structlog.processors.JSONRenderer()]
        if settings.log_as_json
        else [structlog.dev.ConsoleRenderer(colors=(stream or sys.stdout).isatty())]
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[*shared, structlog.stdlib.ExtraAdder(), _drop_color_message],
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, *renderer],
    )

    handler = _HubHandler(stream)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in [h for h in root.handlers if isinstance(h, _HubHandler)]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(settings.app.log_level)

    for name in _UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    for name, level in _QUIET_LOGGERS.items():
        logging.getLogger(name).setLevel(level)

    logging.captureWarnings(True)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger
