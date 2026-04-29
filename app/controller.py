from __future__ import annotations

import logging
import threading
from dataclasses import asdict
from typing import Any, Callable

from app.bot import MarketMakerBot
from app.config import ConfigError, Settings, load_settings
from app.logging_config import setup_logging
from app.polymarket import PolymarketClient
from app.state import BotState, BotStateStore

logger = logging.getLogger(__name__)


class BotController:
    def __init__(
        self,
        settings_loader: Callable[[], Settings] = load_settings,
        client_factory: Callable[[Settings], PolymarketClient] = PolymarketClient,
    ) -> None:
        self._settings_loader = settings_loader
        self._client_factory = client_factory
        self._lock = threading.Lock()
        self._bot: MarketMakerBot | None = None
        self._thread: threading.Thread | None = None
        self._last_error: str | None = None
        self._last_settings: Settings | None = None

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self.is_running():
                return {"ok": True, "message": "Bot already running"}

            try:
                settings = self._settings_loader()
            except ConfigError as exc:
                self._last_error = str(exc)
                return {"ok": False, "message": f"Config error: {exc}"}

            setup_logging(settings)
            bot = MarketMakerBot(settings=settings, client=self._client_factory(settings))
            thread = threading.Thread(target=self._run_bot, args=(bot,), name="market-maker-bot", daemon=True)
            self._bot = bot
            self._thread = thread
            self._last_settings = settings
            self._last_error = None
            thread.start()
            logger.info("Bot started from dashboard", extra={"event": "web_start", "token_id": settings.token_id})
            return {"ok": True, "message": "Bot started"}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            bot = self._bot
            running = self.is_running()
        if not bot or not running:
            return {"ok": True, "message": "Bot already stopped"}

        bot.request_shutdown("web_stop")
        logger.info("Bot stop requested from dashboard", extra={"event": "web_stop", "token_id": bot.settings.token_id})
        return {"ok": True, "message": "Stop requested"}

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict[str, Any]:
        settings = self._get_settings_safe()
        bot = self._bot
        if bot is not None:
            state = BotState(
                known_orders=dict(bot.known_orders),
                last_market_price=bot.last_market_price,
                position_size=bot.position_size,
                average_entry_price=bot.average_entry_price,
                realized_pnl=bot.realized_pnl,
                trades_executed=bot.trades_executed,
                cancel_timestamps=list(bot.cancel_timestamps),
                paused_until=bot.paused_until,
                consecutive_api_errors=bot.consecutive_api_errors,
                last_api_error_time=bot.last_api_error_time,
                last_api_sync_time=bot.last_api_sync_time,
                cycles_without_fill=bot.cycles_without_fill,
                gross_bought=bot.gross_bought,
                gross_sold=bot.gross_sold,
            )
            token_id = bot.settings.token_id
            dry_run = bot.settings.dry_run
            inventory_target = bot.settings.inventory_target
        else:
            state = self._load_persisted_state(settings)
            token_id = settings.token_id if settings else None
            dry_run = settings.dry_run if settings else None
            inventory_target = settings.inventory_target if settings else 0.0

        net_position = state.position_size - inventory_target
        return {
            "running": self.is_running(),
            "token_id": token_id,
            "dry_run": dry_run,
            "last_error": self._last_error,
            "config_loaded": settings is not None,
            "metrics": {
                "last_market_price": state.last_market_price,
                "position_size": round(state.position_size, 4),
                "net_position": round(net_position, 4),
                "average_entry_price": round(state.average_entry_price, 4),
                "realized_pnl": round(state.realized_pnl, 4),
                "trades_executed": state.trades_executed,
                "gross_bought": round(state.gross_bought, 4),
                "gross_sold": round(state.gross_sold, 4),
                "open_orders": len(state.known_orders),
                "cycles_without_fill": state.cycles_without_fill,
                "consecutive_api_errors": state.consecutive_api_errors,
                "paused_until": state.paused_until,
                "last_api_error_time": state.last_api_error_time,
                "last_api_sync_time": state.last_api_sync_time,
            },
            "state": asdict(state),
        }

    def _run_bot(self, bot: MarketMakerBot) -> None:
        try:
            bot.run_forever()
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            logger.exception("Dashboard bot thread crashed", extra={"event": "web_bot_crash", "error": str(exc)})
        finally:
            try:
                bot._save_state()
            except Exception:  # noqa: BLE001
                logger.exception("Failed to persist bot state after dashboard stop", extra={"event": "web_state_save_failed"})
            with self._lock:
                self._bot = None
                self._thread = None

    def _get_settings_safe(self) -> Settings | None:
        try:
            self._last_settings = self._settings_loader()
        except ConfigError as exc:
            self._last_error = str(exc)
        return self._last_settings

    def _load_persisted_state(self, settings: Settings | None) -> BotState:
        if settings is None:
            return BotState(known_orders={})
        return BotStateStore(settings.state_file).load()
