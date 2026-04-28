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
    token_ids: tuple[str, ...]
    spread: float
    size: float
    loop_interval_seconds: int
    tick_size: str
    price_tolerance: float
    min_collateral_buffer: float
    target_position_size: float
    max_position_imbalance: float
    max_position_size: float
    max_balance_usage_pct: float
    price_refresh_threshold: float
    auto_select_market: bool
    market_scan_limit: int
    dynamic_spread_enabled: bool
    min_spread: float
    max_spread: float
    liquidity_target_low: float
    liquidity_target_high: float
    low_volatility_threshold: float
    high_volatility_threshold: float
    low_volatility_spread_multiplier: float
    high_volatility_spread_multiplier: float
    max_retries: int
    retry_delay_seconds: float
    log_level: str
    log_format: str
    state_file: str
    # KILL SWITCH: Safety mechanisms
    max_consecutive_errors: int
    min_balance_threshold: float
    max_api_failure_streak: int
    kill_switch_flag_file: str
    # REPOSITIONING: Order churn reduction
    reposition_price_threshold: float
    # PERFORMANCE: Metrics tracking
    metrics_log_interval_seconds: int
    # PROMPT 3: Safe hedge controls
    hedge_min_position_size: float
    hedge_delay_seconds: float
    # PROMPT 4: Price sanity checks
    min_price_bound: float
    max_price_bound: float
    max_book_spread_pct: float
    max_midpoint_deviation_ratio: float
    # PROMPT 5: Hard sync mode
    max_sync_age_seconds: float
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


def _parse_csv_env(name: str) -> tuple[str, ...]:
    value = os.getenv(name, "")
    items = [item.strip() for item in value.split(",") if item.strip()]
    return tuple(dict.fromkeys(items))


def load_settings() -> Settings:
    load_dotenv()

    try:
        return Settings(
            host=os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com"),
            chain_id=int(os.getenv("CHAIN_ID", "137")),
            private_key=_required("PK"),
            token_id=_required("TOKEN_ID"),
            token_ids=_parse_csv_env("TOKEN_IDS"),
            spread=float(os.getenv("SPREAD", "0.02")),
            size=float(os.getenv("SIZE", "10")),
            loop_interval_seconds=int(os.getenv("LOOP_INTERVAL_SECONDS", "300")),
            tick_size=os.getenv("TICK_SIZE", "0.01"),
            price_tolerance=float(os.getenv("PRICE_TOLERANCE", "0.0001")),
            min_collateral_buffer=float(os.getenv("MIN_COLLATERAL_BUFFER", "1.0")),
            target_position_size=float(os.getenv("TARGET_POSITION_SIZE", "0")),
            max_position_imbalance=float(os.getenv("MAX_POSITION_IMBALANCE", os.getenv("SIZE", "10"))),
            max_position_size=float(os.getenv("MAX_POSITION_SIZE", os.getenv("SIZE", "10"))),
            max_balance_usage_pct=float(os.getenv("MAX_BALANCE_USAGE_PCT", "0.9")),
            price_refresh_threshold=float(os.getenv("PRICE_REFRESH_THRESHOLD", "0.01")),
            auto_select_market=_parse_bool("AUTO_SELECT_MARKET", True),
            market_scan_limit=int(os.getenv("MARKET_SCAN_LIMIT", "25")),
            dynamic_spread_enabled=_parse_bool("DYNAMIC_SPREAD_ENABLED", True),
            min_spread=float(os.getenv("MIN_SPREAD", os.getenv("SPREAD", "0.02"))),
            max_spread=float(os.getenv("MAX_SPREAD", str(max(float(os.getenv("SPREAD", "0.02")) * 2, 0.02)))),
            liquidity_target_low=float(os.getenv("LIQUIDITY_TARGET_LOW", os.getenv("SIZE", "10"))),
            liquidity_target_high=float(os.getenv("LIQUIDITY_TARGET_HIGH", str(max(float(os.getenv("SIZE", "10")) * 5, 10.0)))),
            low_volatility_threshold=float(os.getenv("LOW_VOLATILITY_THRESHOLD", "0.002")),
            high_volatility_threshold=float(os.getenv("HIGH_VOLATILITY_THRESHOLD", "0.01")),
            low_volatility_spread_multiplier=float(os.getenv("LOW_VOLATILITY_SPREAD_MULTIPLIER", "0.85")),
            high_volatility_spread_multiplier=float(os.getenv("HIGH_VOLATILITY_SPREAD_MULTIPLIER", "1.4")),
            max_retries=int(os.getenv("MAX_RETRIES", "3")),
            retry_delay_seconds=float(os.getenv("RETRY_DELAY_SECONDS", "2")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_format=os.getenv("LOG_FORMAT", "text"),
            state_file=os.getenv("STATE_FILE", "bot_state.json"),
            # KILL SWITCH
            max_consecutive_errors=int(os.getenv("MAX_CONSECUTIVE_ERRORS", "5")),
            min_balance_threshold=float(os.getenv("MIN_BALANCE_THRESHOLD", "10.0")),
            max_api_failure_streak=int(os.getenv("MAX_API_FAILURE_STREAK", "3")),
            kill_switch_flag_file=os.getenv("KILL_SWITCH_FLAG_FILE", ".kill_switch"),
            # REPOSITIONING
            reposition_price_threshold=float(os.getenv("REPOSITION_PRICE_THRESHOLD", "0.001")),
            # PERFORMANCE
            metrics_log_interval_seconds=int(os.getenv("METRICS_LOG_INTERVAL_SECONDS", "3600")),
            # PROMPT 3: Safe hedge controls
            hedge_min_position_size=float(os.getenv("HEDGE_MIN_POSITION_SIZE", "5.0")),
            hedge_delay_seconds=float(os.getenv("HEDGE_DELAY_SECONDS", "30.0")),
            # PROMPT 4: Price sanity checks
            min_price_bound=float(os.getenv("MIN_PRICE_BOUND", "0.05")),
            max_price_bound=float(os.getenv("MAX_PRICE_BOUND", "0.95")),
            max_book_spread_pct=float(os.getenv("MAX_BOOK_SPREAD_PCT", "0.02")),
            max_midpoint_deviation_ratio=float(os.getenv("MAX_MIDPOINT_DEVIATION_RATIO", "0.25")),
            # PROMPT 5: Hard sync mode
            max_sync_age_seconds=float(os.getenv("MAX_SYNC_AGE_SECONDS", "60.0")),
            api_key=os.getenv("CLOB_API_KEY"),
            api_secret=os.getenv("CLOB_SECRET"),
            api_passphrase=os.getenv("CLOB_PASS_PHRASE"),
        )
    except ValueError as exc:
        raise ConfigError(f"Invalid env var format: {exc}") from exc
