from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

SAFE_CONFIG_FIELDS: dict[str, str] = {
    "SPREAD": "float",
    "SIZE": "float",
    "LOOP_INTERVAL_SECONDS": "int",
    "PRICE_TOLERANCE": "float",
    "REPOSITION_PRICE_THRESHOLD": "float",
    "MIN_COLLATERAL_BUFFER": "float",
    "MAX_BALANCE_USAGE_PCT": "float",
    "MIN_BALANCE_THRESHOLD": "float",
    "MIN_PRICE_BOUND": "float",
    "MAX_PRICE_BOUND": "float",
    "MAX_BOOK_SPREAD_PCT": "float",
    "MAX_MIDPOINT_DEVIATION_RATIO": "float",
    "MAX_SYNC_AGE_SECONDS": "float",
    "MIN_OPERABLE_SPREAD": "float",
    "MAX_OPERABLE_SPREAD": "float",
    "MAX_CANCELS_PER_MINUTE": "int",
    "PAUSE_AFTER_CANCEL_LIMIT_SECONDS": "float",
    "MAX_ORDER_AGE_SECONDS": "float",
    "MAX_ORDERS_PER_CYCLE": "int",
    "MIN_LIQUIDITY_SCORE": "float",
    "MAX_VOLATILITY_RATIO": "float",
    "FEE_RATE": "float",
    "ESTIMATED_SLIPPAGE_RATE": "float",
    "MIN_PROFIT_MARGIN": "float",
    "CANCEL_REPLACE_PRICE_THRESHOLD": "float",
    "CANCEL_REPLACE_MIN_IMPROVEMENT": "float",
    "INVENTORY_TARGET": "float",
    "INVENTORY_SOFT_LIMIT": "float",
    "INVENTORY_PRICE_ADJUSTMENT": "float",
    "INVENTORY_TIER_1_SIZE": "float",
    "INVENTORY_TIER_1_ADJUSTMENT": "float",
    "INVENTORY_TIER_2_SIZE": "float",
    "INVENTORY_TIER_2_ADJUSTMENT": "float",
    "INVENTORY_TIER_3_SIZE": "float",
    "INVENTORY_TIER_3_ADJUSTMENT": "float",
    "COOLDOWN_AFTER_FILL_SECONDS": "float",
    "DAILY_LOSS_LIMIT": "float",
    "DAILY_VOLUME_LIMIT": "float",
    "DEAD_MARKET_CYCLE_LIMIT": "int",
    "DEAD_MARKET_PAUSE_SECONDS": "float",
    "DEAD_MARKET_PRICE_TOLERANCE_RATIO": "float",
    "PROTECTION_NO_FILL_CYCLES": "int",
    "PROTECTION_ERROR_STREAK": "int",
    "PROTECTION_PAUSE_SECONDS": "float",
    "PROTECTION_SPREAD_MULTIPLIER": "float",
    "DRY_RUN": "bool",
    "WEB_HOST": "str",
    "WEB_PORT": "int",
    "ALERT_ON_FILL": "bool",
    "ALERT_ON_PAUSE": "bool",
    "ALERT_ON_ERROR": "bool",
}


class EnvManager:
    def __init__(self, path: str = ".env") -> None:
        self.path = Path(path)

    def read_safe(self) -> dict[str, str]:
        raw = {key: value for key, value in dotenv_values(self.path).items() if value is not None}
        return {key: str(raw.get(key, "")) for key in SAFE_CONFIG_FIELDS}

    def write_safe(self, updates: dict[str, Any]) -> dict[str, str]:
        current = {key: value for key, value in dotenv_values(self.path).items() if value is not None}
        for key, value in updates.items():
            if key not in SAFE_CONFIG_FIELDS:
                continue
            current[key] = self._normalize(key, value)
            os.environ[key] = current[key]
        lines = [f"{key}={value}" for key, value in current.items()]
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {key: str(current.get(key, "")) for key in SAFE_CONFIG_FIELDS}

    def _normalize(self, key: str, value: Any) -> str:
        kind = SAFE_CONFIG_FIELDS[key]
        if kind == "bool":
            text = str(value).strip().lower()
            return "true" if text in {"1", "true", "yes", "on"} else "false"
        if kind == "int":
            return str(int(float(value)))
        if kind == "float":
            return str(float(value))
        return str(value)
