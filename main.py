from __future__ import annotations

import logging
import signal

from app.bot import MarketMakerBot
from app.config import ConfigError, load_settings
from app.logging_config import setup_logging
from app.market_data import PolymarketMarketStream
from app.polymarket import PolymarketApiError, PolymarketClient

logger = logging.getLogger(__name__)


def _install_signal_handlers(bot: MarketMakerBot) -> None:
    def _handle_signal(signum: int, _frame: object) -> None:
        signal_name = signal.Signals(signum).name
        logger.warning(
            "Received process signal",
            extra={"event": "process_signal", "signal": signal_name, "token_id": bot.settings.token_id},
        )
        bot.request_shutdown(reason=signal_name.lower())

    for signal_name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, signal_name):
            signal.signal(getattr(signal, signal_name), _handle_signal)


def main() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        raise SystemExit(f"Erro de configuração: {exc}") from exc

    setup_logging(settings)

    try:
        client = PolymarketClient(settings)
        market_stream = PolymarketMarketStream(settings) if settings.enable_websocket else None
        bot = MarketMakerBot(settings=settings, client=client, market_stream=market_stream)
        _install_signal_handlers(bot)
        bot.run_forever()
    except PolymarketApiError as exc:
        logger.exception("Erro fatal de API", extra={"event": "fatal_api", "error": str(exc)})
        raise SystemExit(2) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Erro fatal não tratado", extra={"event": "fatal_unexpected", "error": str(exc)})
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
