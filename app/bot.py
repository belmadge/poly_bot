from __future__ import annotations

import logging
import time

from app.config import Settings
from app.polymarket import PolymarketClient


logger = logging.getLogger(__name__)


class MarketMakerBot:
    def __init__(self, settings: Settings, client: PolymarketClient) -> None:
        self.settings = settings
        self.client = client

    def run_once(self) -> None:
        current_price = self.client.get_current_price(self.settings.token_id)

        half_spread = self.settings.spread / 2
        buy_price = max(0.001, round(current_price - half_spread, 4))
        sell_price = min(0.999, round(current_price + half_spread, 4))

        logger.info(
            "Preço atual=%.4f | buy=%.4f | sell=%.4f | size=%.4f",
            current_price,
            buy_price,
            sell_price,
            self.settings.size,
        )

        buy_resp = self.client.place_limit_order(
            token_id=self.settings.token_id,
            side="buy",
            price=buy_price,
            size=self.settings.size,
        )
        logger.info("Ordem BUY enviada: %s", buy_resp)

        sell_resp = self.client.place_limit_order(
            token_id=self.settings.token_id,
            side="sell",
            price=sell_price,
            size=self.settings.size,
        )
        logger.info("Ordem SELL enviada: %s", sell_resp)

    def run_forever(self) -> None:
        logger.info(
            "Iniciando bot market maker. Intervalo=%ss, token_id=%s",
            self.settings.loop_interval_seconds,
            self.settings.token_id,
        )
        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                logger.info("Bot interrompido pelo usuário.")
                break
            except Exception as exc:  # noqa: BLE001
                logger.exception("Erro no loop principal: %s", exc)
            finally:
                time.sleep(self.settings.loop_interval_seconds)
