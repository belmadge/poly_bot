from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class BotState:
    known_orders: dict[str, dict[str, Any]]
    net_positions: dict[str, float]
    last_market_prices: dict[str, float]


class BotStateStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def load(self) -> BotState:
        if not self.path.exists():
            return BotState(known_orders={}, net_positions={}, last_market_prices={})

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        known_orders = payload.get("known_orders", {})
        net_positions = {str(key): float(value) for key, value in payload.get("net_positions", {}).items()}
        last_market_prices = {str(key): float(value) for key, value in payload.get("last_market_prices", {}).items()}
        return BotState(known_orders=known_orders, net_positions=net_positions, last_market_prices=last_market_prices)

    def save(self, state: BotState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "known_orders": state.known_orders,
            "net_positions": state.net_positions,
            "last_market_prices": state.last_market_prices,
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
