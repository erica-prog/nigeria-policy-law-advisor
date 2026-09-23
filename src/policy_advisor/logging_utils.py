"""Structured JSON logging. One log line per stage, correlated by request_id,
so a query can be reconstructed after the fact instead of only debugged live."""

import json
import logging
import sys
import time
import uuid
from contextlib import contextmanager


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def get_logger(name: str, level: str = "info") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(level.upper())
        logger.propagate = False
    return logger


def log_event(logger: logging.Logger, message: str, **fields) -> None:
    logger.info(message, extra={"fields": fields})


@contextmanager
def timed_request(logger: logging.Logger, query: str):
    """Yields a dict the caller fills in as stages complete, then emits one
    correlated summary line — latency breakdown and outcome in a single record."""
    request_id = str(uuid.uuid4())
    fields: dict = {"request_id": request_id, "query": query}
    start = time.monotonic()
    try:
        yield fields
    except Exception as exc:
        fields["error"] = str(exc)
        raise
    finally:
        fields["total_latency_ms"] = round((time.monotonic() - start) * 1000, 1)
        log_event(logger, "query_completed", **fields)
