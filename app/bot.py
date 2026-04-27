from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.polymarket import CLOSED_STATUSES, FILLED_STATUSES, PolymarketClient


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Quote:
    side: str
    price: float
    size: float


class MarketMakerBot:
    def __init__(self, settings: Settings, client: PolymarketClient) -> None:
        self.settings = settings
        self.client = client
        self.known_order_ids: set[str] = set()

    def run_once(self) -> None:
        current_price = self.client.get_current_price(self.settings.token_id)
        quotes = self._build_quotes(current_price)

        logger.info(
            "Preço atual=%.4f | buy=%.4f | sell=%.4f | size=%.4f",
            current_price,
            quotes["buy"].price,
            quotes["sell"].price,
            self.settings.size,
        )

        self._check_fills()

        open_orders = self.client.get_open_orders(self.settings.token_id)
        reusable_orders, order_ids_to_cancel = self._split_reusable_orders(open_orders, quotes)

        if order_ids_to_cancel:
            cancel_resp = self.client.cancel_orders(order_ids_to_cancel)
            logger.info("Ordens antigas canceladas: %s", cancel_resp)

        if not self._has_buy_balance(quotes["buy"]):
            logger.warning("Saldo insuficiente para BUY, ordem pulada.")
        elif reusable_orders.get("buy"):
            logger.info("BUY já existe no preço alvo; evitando duplicação.")
        else:
            buy_resp = self.client.place_limit_order(
                token_id=self.settings.token_id,
                side="buy",
                price=quotes["buy"].price,
                size=quotes["buy"].size,
            )
            logger.info("Ordem BUY enviada: %s", buy_resp)
            self._track_order_id(buy_resp)

        if not self._has_sell_balance(quotes["sell"]):
            logger.warning("Saldo/posição insuficiente para SELL, ordem pulada.")
        elif reusable_orders.get("sell"):
            logger.info("SELL já existe no preço alvo; evitando duplicação.")
        else:
            sell_resp = self.client.place_limit_order(
                token_id=self.settings.token_id,
                side="sell",
                price=quotes["sell"].price,
                size=quotes["sell"].size,
            )
            logger.info("Ordem SELL enviada: %s", sell_resp)
            self._track_order_id(sell_resp)

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

    def _build_quotes(self, current_price: float) -> dict[str, Quote]:
        half_spread = self.settings.spread / 2
        buy_price = max(0.001, round(current_price - half_spread, 4))
        sell_price = min(0.999, round(current_price + half_spread, 4))
        return {
            "buy": Quote(side="buy", price=buy_price, size=self.settings.size),
            "sell": Quote(side="sell", price=sell_price, size=self.settings.size),
        }

    def _split_reusable_orders(
        self, open_orders: list[dict[str, Any]], quotes: dict[str, Quote]
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        reusable: dict[str, dict[str, Any]] = {}
        to_cancel: list[str] = []

        for order in open_orders:
            order_id = self.client.extract_order_id(order)
            side = self._extract_side(order)

            if not order_id or side not in quotes:
                continue

            if self._matches_quote(order, quotes[side]) and side not in reusable:
                reusable[side] = order
                self.known_order_ids.add(order_id)
                continue

            to_cancel.append(order_id)

        return reusable, to_cancel

    def _check_fills(self) -> None:
        if not self.known_order_ids:
            return

        still_open: set[str] = set()
        for order_id in self.known_order_ids:
            details = self.client.get_order_details(order_id)
            if not details:
                still_open.add(order_id)
                continue

            status = self._extract_status(details)
            if status in FILLED_STATUSES:
                logger.info("Ordem executada (filled): order_id=%s | detalhes=%s", order_id, details)
            elif status in CLOSED_STATUSES:
                logger.info("Ordem encerrada: order_id=%s | status=%s", order_id, status)
            else:
                still_open.add(order_id)

        self.known_order_ids = still_open

    def _has_buy_balance(self, quote: Quote) -> bool:
        collateral = self.client.get_collateral_balance()
        if collateral is None:
            logger.warning("Não foi possível validar saldo de collateral; prosseguindo com BUY.")
            return True

        required = (quote.price * quote.size) + self.settings.min_collateral_buffer
        logger.info("Collateral disponível=%.4f | necessário BUY=%.4f", collateral, required)
        return collateral >= required

    def _has_sell_balance(self, quote: Quote) -> bool:
        token_balance = self.client.get_token_balance(self.settings.token_id)
        if token_balance is None:
            logger.warning("Não foi possível validar posição do token; prosseguindo com SELL.")
            return True

        logger.info("Posição token disponível=%.4f | necessário SELL=%.4f", token_balance, quote.size)
        return token_balance >= quote.size

    def _matches_quote(self, order: dict[str, Any], quote: Quote) -> bool:
        order_price = self._extract_price(order)
        order_size = self._extract_size(order)

        if order_price is None or order_size is None:
            return False

        price_diff = abs(order_price - quote.price)
        size_diff = abs(order_size - quote.size)
        return price_diff <= self.settings.price_tolerance and size_diff <= self.settings.price_tolerance

    @staticmethod
    def _extract_status(order: dict[str, Any]) -> str:
        for key in ("status", "state", "order_status"):
            value = order.get(key)
            if value is not None:
                return str(value).lower()
        return ""

    @staticmethod
    def _extract_price(order: dict[str, Any]) -> float | None:
        for key in ("price", "limit_price"):
            value = order.get(key)
            if value is not None:
                return float(value)
        return None

    @staticmethod
    def _extract_size(order: dict[str, Any]) -> float | None:
        for key in ("size", "quantity", "original_size"):
            value = order.get(key)
            if value is not None:
                return float(value)
        return None

    @staticmethod
    def _extract_side(order: dict[str, Any]) -> str:
        value = order.get("side")
        if value is None:
            return ""
        side = str(value).lower()
        if side in {"buy", "bid"}:
            return "buy"
        if side in {"sell", "ask"}:
            return "sell"
        return ""

    def _track_order_id(self, response: dict[str, Any]) -> None:
        order_id = self.client.extract_order_id(response)
        if order_id:
            self.known_order_ids.add(order_id)
