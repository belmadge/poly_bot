from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class BotState:
    known_orders: dict[str, dict[str, Any]]
    last_market_price: float | None = None
    consecutive_api_errors: int = 0
    last_api_error_time: float = 0.0
    last_api_sync_time: float = 0.0


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
            consecutive_api_errors=int(payload.get("consecutive_api_errors", 0)),
            last_api_error_time=float(payload.get("last_api_error_time", 0.0)),
            last_api_sync_time=float(payload.get("last_api_sync_time", 0.0)),
        )

    def save(self, state: BotState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "known_orders": state.known_orders,
            "last_market_price": state.last_market_price,
            "consecutive_api_errors": state.consecutive_api_errors,
            "last_api_error_time": state.last_api_error_time,
            "last_api_sync_time": state.last_api_sync_time,
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
