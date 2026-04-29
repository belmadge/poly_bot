from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

_MAX_LOG_RECORDS = 500
_buffer_lock = threading.Lock()
_records: deque[dict[str, Any]] = deque(maxlen=_MAX_LOG_RECORDS)


class MemoryLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "event": getattr(record, "event", "log"),
            "message": record.getMessage(),
            "token_id": getattr(record, "token_id", "-"),
        }
        for key in (
            "reason",
            "side",
            "price",
            "size",
            "spread",
            "book_spread",
            "liquidity_score",
            "volatility_ratio",
            "position_size",
            "realized_pnl",
            "gross_bought",
            "gross_sold",
            "error",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        with _buffer_lock:
            _records.append(payload)


def get_recent_logs(limit: int = 100) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(limit, _MAX_LOG_RECORDS))
    with _buffer_lock:
        return list(_records)[-bounded_limit:]
