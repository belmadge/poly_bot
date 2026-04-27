from __future__ import annotations

import logging
import sys

from app.config import Settings


class ContextFilter(logging.Filter):
    def __init__(self, token_id: str) -> None:
        super().__init__()
        self.token_id = token_id

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "token_id"):
            record.token_id = self.token_id
        return True


def setup_logging(settings: Settings) -> None:
    root = logging.getLogger()
    root.handlers.clear()

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    formatter = _build_formatter(settings.log_format)
    handler.setFormatter(formatter)
    handler.addFilter(ContextFilter(token_id=settings.token_id))
    root.addHandler(handler)


def _build_formatter(log_format: str) -> logging.Formatter:
    if log_format.lower() == "json":
        try:
            from pythonjsonlogger import jsonlogger

            return jsonlogger.JsonFormatter(
                fmt="%(asctime)s %(levelname)s %(name)s %(message)s %(token_id)s"
            )
        except Exception:  # noqa: BLE001
            pass

    return logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | token=%(token_id)s | %(message)s"
    )
