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
            api_key=os.getenv("CLOB_API_KEY"),
            api_secret=os.getenv("CLOB_SECRET"),
            api_passphrase=os.getenv("CLOB_PASS_PHRASE"),
        )
    except ValueError as exc:
        raise ConfigError(f"Invalid env var format: {exc}") from exc
