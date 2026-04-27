from __future__ import annotations

import logging

from app.bot import MarketMakerBot
from app.config import ConfigError, load_settings
from app.polymarket import PolymarketClient


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def main() -> None:
    setup_logging()

    try:
        settings = load_settings()
    except ConfigError as exc:
        raise SystemExit(f"Erro de configuração: {exc}") from exc

    client = PolymarketClient(settings)
    bot = MarketMakerBot(settings=settings, client=client)
    bot.run_forever()


if __name__ == "__main__":
    main()
