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
            dry_run=_parse_bool("DRY_RUN", False),
            api_key=os.getenv("CLOB_API_KEY"),
            api_secret=os.getenv("CLOB_SECRET"),
            api_passphrase=os.getenv("CLOB_PASS_PHRASE"),
        )
    except ValueError as exc:
        raise ConfigError(f"Invalid env var format: {exc}") from exc
