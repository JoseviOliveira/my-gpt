"""Shared logging configuration helpers."""

from __future__ import annotations

import logging
import sys
from flask import g, has_request_context

LOG_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_FORMAT = "%(asctime)s [%(levelname)s] req=%(req_id)s user=%(username)s %(name)s: %(message)s"


class RequestContextFilter(logging.Filter):
    """Inject request context (req_id, username, session_id) into log records."""

    def filter(self, record):
        """Add request context attributes to the log record."""
        if has_request_context():
            record.req_id = getattr(g, 'req_id', '-')
            record.username = getattr(g, 'username', '-')
            record.session_id = getattr(g, 'session_id', '-')
        else:
            record.req_id = '-'
            record.username = '-'
            record.session_id = '-'
        return True


class MaxLevelFilter(logging.Filter):
    """Allow log records up to a maximum level."""

    def __init__(self, max_level: int) -> None:
        super().__init__()
        self.max_level = max_level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= self.max_level


def ensure_structured_logging(log_level: int = logging.INFO) -> logging.Formatter:
    """Ensure root + werkzeug loggers emit timestamps in the desired format.
    
    Args:
        log_level: The logging level to set (from config.LOG_LEVEL)
    """
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_TIME_FORMAT)
    context_filter = RequestContextFilter()
    root = logging.getLogger()

    for handler in list(root.handlers):
        root.removeHandler(handler)

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(log_level)
    stdout_handler.addFilter(MaxLevelFilter(logging.WARNING - 1))
    stdout_handler.setFormatter(formatter)
    stdout_handler.addFilter(context_filter)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(formatter)
    stderr_handler.addFilter(context_filter)

    root.addHandler(stdout_handler)
    root.addHandler(stderr_handler)
    root.setLevel(log_level)

    # Suppress noisy third-party library DEBUG logs
    logging.getLogger("torio").setLevel(logging.WARNING)
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)
    logging.getLogger("numba").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.INFO)
    logging.getLogger("jieba").setLevel(logging.WARNING)

    werkzeug_logger = logging.getLogger("werkzeug")
    for handler in list(werkzeug_logger.handlers):
        werkzeug_logger.removeHandler(handler)
    werkzeug_logger.setLevel(max(log_level, logging.WARNING))
    werkzeug_logger.propagate = True

    return formatter


__all__ = ["ensure_structured_logging", "LOG_TIME_FORMAT", "LOG_FORMAT"]
