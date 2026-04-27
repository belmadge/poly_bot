from __future__ import annotations

import logging

from app.bot import MarketMakerBot
from app.config import ConfigError, load_settings
from app.logging_config import setup_logging
from app.polymarket import PolymarketApiError, PolymarketClient

logger = logging.getLogger(__name__)


def main() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        raise SystemExit(f"Erro de configuração: {exc}") from exc

    setup_logging(settings)

    try:
        client = PolymarketClient(settings)
        bot = MarketMakerBot(settings=settings, client=client)
        bot.run_forever()
    except PolymarketApiError as exc:
        logger.exception("Erro fatal de API", extra={"event": "fatal_api", "error": str(exc)})
        raise SystemExit(2) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Erro fatal não tratado", extra={"event": "fatal_unexpected", "error": str(exc)})
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
