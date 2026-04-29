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
        if not hasattr(record, "event"):
            record.event = "log"
        return True


class TerminalFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record, self.datefmt)
        level = record.levelname.ljust(7)
        token_id = getattr(record, "token_id", "-")
        event = getattr(record, "event", "log")
        message = record.getMessage()
        details = self._build_details(record)
        base = f"{timestamp} | {level} | {event} | token={token_id} | {message}"
        if details:
            base = f"{base} | {details}"
        if record.exc_info:
            return f"{base}\n{self.formatException(record.exc_info)}"
        return base

    def _build_details(self, record: logging.LogRecord) -> str:
        extras: list[str] = []
        for key in (
            "side",
            "price",
            "previous_price",
            "price_change_ratio",
            "size",
            "buy_size",
            "sell_size",
            "order_id",
            "count",
            "spread",
            "book_spread",
            "liquidity_score",
            "volatility_multiplier",
            "volatility_ratio",
            "refresh_threshold",
            "net_position",
            "local_net_position",
            "projected_exposure",
            "max_position_size",
            "collateral",
            "token_balance",
            "max_usable_collateral",
            "max_usable_tokens",
            "required",
            "reason",
            "spread_multiplier",
            "estimated_cost",
            "required_spread",
            "cycles_without_fill",
            "gross_bought",
            "gross_sold",
            "position_size",
            "realized_pnl",
            "attempt",
            "error",
            "status",
        ):
            value = getattr(record, key, None)
            if value is not None:
                extras.append(f"{key}={value}")
        return " ".join(extras)


def setup_logging(settings: Settings) -> None:
    root = logging.getLogger()
    root.handlers.clear()

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_build_formatter(settings.log_format))
    handler.addFilter(ContextFilter(token_id=settings.token_id))
    root.addHandler(handler)


def _build_formatter(log_format: str) -> logging.Formatter:
    if log_format.lower() == "json":
        try:
            from pythonjsonlogger import jsonlogger

            return jsonlogger.JsonFormatter(
                fmt="%(asctime)s %(levelname)s %(name)s %(message)s %(event)s %(token_id)s"
            )
        except Exception:  # noqa: BLE001
            pass

    return TerminalFormatter(datefmt="%Y-%m-%d %H:%M:%S")
