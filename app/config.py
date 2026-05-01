from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    host: str
    chain_id: int
    private_key: str
    token_id: str
    spread: float
    size: float
    loop_interval_seconds: int
    tick_size: str
    price_tolerance: float
    min_collateral_buffer: float
    max_balance_usage_pct: float
    max_retries: int
    retry_delay_seconds: float
    log_level: str
    log_format: str
    state_file: str
    max_consecutive_errors: int
    min_balance_threshold: float
    max_api_failure_streak: int
    kill_switch_flag_file: str
    reposition_price_threshold: float
    min_price_bound: float
    max_price_bound: float
    max_book_spread_pct: float
    max_midpoint_deviation_ratio: float
    max_sync_age_seconds: float
    min_operable_spread: float
    max_operable_spread: float
    max_cancels_per_minute: int
    pause_after_cancel_limit_seconds: float
    max_order_age_seconds: float
    max_orders_per_cycle: int
    min_liquidity_score: float
    max_volatility_ratio: float
    fee_rate: float
    estimated_slippage_rate: float
    min_profit_margin: float
    inventory_target: float
    inventory_soft_limit: float
    inventory_price_adjustment: float
    protection_no_fill_cycles: int
    protection_error_streak: int
    protection_pause_seconds: float
    protection_spread_multiplier: float
    enable_websocket: bool
    ws_market_url: str
    ws_reconnect_seconds: float
    event_debounce_seconds: float
    event_idle_poll_seconds: float
    dry_run: bool
    api_key: str | None = None
    api_secret: str | None = None
    api_passphrase: str | None = None


class ConfigError(ValueError):
    """Raised when required environment variables are missing or invalid."""


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def _parse_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    load_dotenv()

    try:
        return Settings(
            host=os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com"),
            chain_id=int(os.getenv("CHAIN_ID", "137")),
            private_key=_required("PK"),
            token_id=_required("TOKEN_ID"),
            spread=float(os.getenv("SPREAD", "0.02")),
            size=float(os.getenv("SIZE", "10")),
            loop_interval_seconds=int(os.getenv("LOOP_INTERVAL_SECONDS", "300")),
            tick_size=os.getenv("TICK_SIZE", "0.01"),
            price_tolerance=float(os.getenv("PRICE_TOLERANCE", "0.0001")),
            min_collateral_buffer=float(os.getenv("MIN_COLLATERAL_BUFFER", "1.0")),
            max_balance_usage_pct=float(os.getenv("MAX_BALANCE_USAGE_PCT", "0.9")),
            max_retries=int(os.getenv("MAX_RETRIES", "3")),
            retry_delay_seconds=float(os.getenv("RETRY_DELAY_SECONDS", "2")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_format=os.getenv("LOG_FORMAT", "text"),
            state_file=os.getenv("STATE_FILE", "bot_state.json"),
            max_consecutive_errors=int(os.getenv("MAX_CONSECUTIVE_ERRORS", "5")),
            min_balance_threshold=float(os.getenv("MIN_BALANCE_THRESHOLD", "10.0")),
            max_api_failure_streak=int(os.getenv("MAX_API_FAILURE_STREAK", "3")),
            kill_switch_flag_file=os.getenv("KILL_SWITCH_FLAG_FILE", ".kill_switch"),
            reposition_price_threshold=float(os.getenv("REPOSITION_PRICE_THRESHOLD", "0.001")),
            min_price_bound=float(os.getenv("MIN_PRICE_BOUND", "0.05")),
            max_price_bound=float(os.getenv("MAX_PRICE_BOUND", "0.95")),
            max_book_spread_pct=float(os.getenv("MAX_BOOK_SPREAD_PCT", "0.02")),
            max_midpoint_deviation_ratio=float(os.getenv("MAX_MIDPOINT_DEVIATION_RATIO", "0.25")),
            max_sync_age_seconds=float(os.getenv("MAX_SYNC_AGE_SECONDS", "60.0")),
            min_operable_spread=float(os.getenv("MIN_OPERABLE_SPREAD", "0.01")),
            max_operable_spread=float(os.getenv("MAX_OPERABLE_SPREAD", "0.20")),
            max_cancels_per_minute=int(os.getenv("MAX_CANCELS_PER_MINUTE", "6")),
            pause_after_cancel_limit_seconds=float(os.getenv("PAUSE_AFTER_CANCEL_LIMIT_SECONDS", "60.0")),
            max_order_age_seconds=float(os.getenv("MAX_ORDER_AGE_SECONDS", "300.0")),
            max_orders_per_cycle=int(os.getenv("MAX_ORDERS_PER_CYCLE", "2")),
            min_liquidity_score=float(os.getenv("MIN_LIQUIDITY_SCORE", "100.0")),
            max_volatility_ratio=float(os.getenv("MAX_VOLATILITY_RATIO", "0.05")),
            fee_rate=float(os.getenv("FEE_RATE", "0.0015")),
            estimated_slippage_rate=float(os.getenv("ESTIMATED_SLIPPAGE_RATE", "0.001")),
            min_profit_margin=float(os.getenv("MIN_PROFIT_MARGIN", "0.0025")),
            inventory_target=float(os.getenv("INVENTORY_TARGET", "0.0")),
            inventory_soft_limit=float(os.getenv("INVENTORY_SOFT_LIMIT", "25.0")),
            inventory_price_adjustment=float(os.getenv("INVENTORY_PRICE_ADJUSTMENT", "0.003")),
            protection_no_fill_cycles=int(os.getenv("PROTECTION_NO_FILL_CYCLES", "4")),
            protection_error_streak=int(os.getenv("PROTECTION_ERROR_STREAK", "2")),
            protection_pause_seconds=float(os.getenv("PROTECTION_PAUSE_SECONDS", "180.0")),
            protection_spread_multiplier=float(os.getenv("PROTECTION_SPREAD_MULTIPLIER", "1.5")),
            enable_websocket=_parse_bool("ENABLE_WEBSOCKET", False),
            ws_market_url=os.getenv("WS_MARKET_URL", "wss://ws-subscriptions-clob.polymarket.com/ws/market"),
            ws_reconnect_seconds=float(os.getenv("WS_RECONNECT_SECONDS", "5.0")),
            event_debounce_seconds=float(os.getenv("EVENT_DEBOUNCE_SECONDS", "0.25")),
            event_idle_poll_seconds=float(os.getenv("EVENT_IDLE_POLL_SECONDS", "30.0")),
            dry_run=_parse_bool("DRY_RUN", False),
            api_key=os.getenv("CLOB_API_KEY"),
            api_secret=os.getenv("CLOB_SECRET"),
            api_passphrase=os.getenv("CLOB_PASS_PHRASE"),
        )
    except ValueError as exc:
        raise ConfigError(f"Invalid env var format: {exc}") from exc
