from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class BotState:
    known_orders: dict[str, dict[str, Any]]
    last_market_price: float | None = None
    position_size: float = 0.0
    average_entry_price: float = 0.0
    realized_pnl: float = 0.0
    trades_executed: int = 0
    cancel_timestamps: list[float] | None = None
    paused_until: float = 0.0
    consecutive_api_errors: int = 0
    last_api_error_time: float = 0.0
    last_api_sync_time: float = 0.0
    cycles_without_fill: int = 0
    gross_bought: float = 0.0
    gross_sold: float = 0.0


class BotStateStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def load(self) -> BotState:
        if not self.path.exists():
            return BotState(known_orders={})

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        known_orders = payload.get("known_orders", {})
        last_market_price = payload.get("last_market_price")
        if last_market_price is None:
            legacy_prices = payload.get("last_market_prices", {})
            if legacy_prices:
                first_value = next(iter(legacy_prices.values()), None)
                last_market_price = float(first_value) if first_value is not None else None

        return BotState(
            known_orders=known_orders,
            last_market_price=float(last_market_price) if last_market_price is not None else None,
            position_size=float(payload.get("position_size", 0.0)),
            average_entry_price=float(payload.get("average_entry_price", 0.0)),
            realized_pnl=float(payload.get("realized_pnl", 0.0)),
            trades_executed=int(payload.get("trades_executed", 0)),
            cancel_timestamps=[float(value) for value in payload.get("cancel_timestamps", [])],
            paused_until=float(payload.get("paused_until", 0.0)),
            consecutive_api_errors=int(payload.get("consecutive_api_errors", 0)),
            last_api_error_time=float(payload.get("last_api_error_time", 0.0)),
            last_api_sync_time=float(payload.get("last_api_sync_time", 0.0)),
            cycles_without_fill=int(payload.get("cycles_without_fill", 0)),
            gross_bought=float(payload.get("gross_bought", 0.0)),
            gross_sold=float(payload.get("gross_sold", 0.0)),
        )

    def save(self, state: BotState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "known_orders": state.known_orders,
            "last_market_price": state.last_market_price,
            "position_size": state.position_size,
            "average_entry_price": state.average_entry_price,
            "realized_pnl": state.realized_pnl,
            "trades_executed": state.trades_executed,
            "cancel_timestamps": state.cancel_timestamps or [],
            "paused_until": state.paused_until,
            "consecutive_api_errors": state.consecutive_api_errors,
            "last_api_error_time": state.last_api_error_time,
            "last_api_sync_time": state.last_api_sync_time,
            "cycles_without_fill": state.cycles_without_fill,
            "gross_bought": state.gross_bought,
            "gross_sold": state.gross_sold,
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
