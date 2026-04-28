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
    # Performance metrics
    trades_executed: int = 0
    total_pnl_realized: float = 0.0
    last_metrics_log_time: float = 0.0
    consecutive_api_errors: int = 0
    last_api_error_time: float = 0.0
    # PROMPT 3: Track last hedge creation time per token for throttling
    last_hedge_creation_time: dict[str, float] = None  # type: ignore
    # PROMPT 5: Track API sync timestamp
    last_api_sync_time: float = 0.0


class BotStateStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def load(self) -> BotState:
        if not self.path.exists():
            return BotState(
                known_orders={},
                net_positions={},
                last_market_prices={},
                trades_executed=0,
                total_pnl_realized=0.0,
                last_metrics_log_time=0.0,
                consecutive_api_errors=0,
                last_api_error_time=0.0,
                last_hedge_creation_time={},
                last_api_sync_time=0.0,
            )

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        known_orders = payload.get("known_orders", {})
        net_positions = {str(key): float(value) for key, value in payload.get("net_positions", {}).items()}
        last_market_prices = {str(key): float(value) for key, value in payload.get("last_market_prices", {}).items()}
        trades_executed = payload.get("trades_executed", 0)
        total_pnl_realized = payload.get("total_pnl_realized", 0.0)
        last_metrics_log_time = payload.get("last_metrics_log_time", 0.0)
        consecutive_api_errors = payload.get("consecutive_api_errors", 0)
        last_api_error_time = payload.get("last_api_error_time", 0.0)
        last_hedge_creation_time = payload.get("last_hedge_creation_time", {})
        last_api_sync_time = payload.get("last_api_sync_time", 0.0)
        return BotState(
            known_orders=known_orders,
            net_positions=net_positions,
            last_market_prices=last_market_prices,
            trades_executed=trades_executed,
            total_pnl_realized=total_pnl_realized,
            last_metrics_log_time=last_metrics_log_time,
            consecutive_api_errors=consecutive_api_errors,
            last_api_error_time=last_api_error_time,
            last_hedge_creation_time=last_hedge_creation_time,
            last_api_sync_time=last_api_sync_time,
        )

    def save(self, state: BotState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "known_orders": state.known_orders,
            "net_positions": state.net_positions,
            "last_market_prices": state.last_market_prices,
            "trades_executed": state.trades_executed,
            "total_pnl_realized": state.total_pnl_realized,
            "last_metrics_log_time": state.last_metrics_log_time,
            "consecutive_api_errors": state.consecutive_api_errors,
            "last_api_error_time": state.last_api_error_time,
            "last_hedge_creation_time": state.last_hedge_creation_time or {},
            "last_api_sync_time": state.last_api_sync_time,
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
