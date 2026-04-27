from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.polymarket import CLOSED_STATUSES, FILLED_STATUSES, PolymarketApiError, PolymarketClient


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
        self.known_orders: dict[str, dict[str, Any]] = {}
        self.current_token_id = settings.token_id

    def run_once(self) -> None:
        market = self._select_market()
        token_id = market["token_id"]
        self.current_token_id = token_id
        spread = self._determine_spread(market)
        current_price = market["midpoint"]
        quotes = self._build_quotes(current_price, spread)

        logger.info(
            "Calculated quotes",
            extra={
                "event": "quotes",
                "token_id": token_id,
                "price": current_price,
                "buy_price": quotes["buy"].price,
                "sell_price": quotes["sell"].price,
                "size": self.settings.size,
                "spread": spread,
                "liquidity_score": market["liquidity_score"],
            },
        )

        filled_orders = self._check_fills()

        token_balance = self._get_token_balance()
        if filled_orders:
            logger.info(
                "Posicao apos execucao",
                extra={"event": "position_after_fill", "token_id": token_id, "token_balance": token_balance, "filled_count": len(filled_orders)},
            )

        open_orders = self.list_all_open_orders()
        if open_orders:
            self.cancel_open_orders(open_orders)

        remaining_open_orders = self.list_open_orders(token_id=token_id)
        reusable_orders = self._find_duplicate_target_orders(remaining_open_orders, quotes)

        if not self._position_allows_side("buy", token_balance):
            logger.info("BUY bloqueado por controle de posicao.", extra={"event": "skip_buy_position"})
        elif not self._has_buy_balance(quotes["buy"]):
            logger.warning("Saldo insuficiente para BUY, ordem pulada.", extra={"event": "skip_buy_no_balance"})
        elif reusable_orders.get("buy"):
            logger.info("BUY já existe no preço alvo; evitando duplicação.", extra={"event": "dedupe_buy"})
        else:
            buy_resp = self._with_retry(
                lambda: self.client.place_limit_order(
                    token_id=token_id,
                    side="buy",
                    price=quotes["buy"].price,
                    size=quotes["buy"].size,
                )
            )
            logger.info("Ordem BUY enviada", extra={"event": "place_buy", "response": buy_resp})
            self._track_order_id(buy_resp)

        if not self._position_allows_side("sell", token_balance):
            logger.info("SELL bloqueado por controle de posicao.", extra={"event": "skip_sell_position"})
        elif not self._has_sell_balance(quotes["sell"]):
            logger.warning(
                "Saldo/posição insuficiente para SELL, ordem pulada.",
                extra={"event": "skip_sell_no_balance"},
            )
        elif reusable_orders.get("sell"):
            logger.info("SELL já existe no preço alvo; evitando duplicação.", extra={"event": "dedupe_sell"})
        else:
            sell_resp = self._with_retry(
                lambda: self.client.place_limit_order(
                    token_id=token_id,
                    side="sell",
                    price=quotes["sell"].price,
                    size=quotes["sell"].size,
                )
            )
            logger.info("Ordem SELL enviada", extra={"event": "place_sell", "response": sell_resp})
            self._track_order_id(sell_resp)

    def run_forever(self) -> None:
        logger.info(
            "Starting market maker bot",
            extra={
                "event": "startup",
                "interval_seconds": self.settings.loop_interval_seconds,
                "token_id": self.settings.token_id,
            },
        )
        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                logger.info("Bot interrompido pelo usuário.", extra={"event": "shutdown"})
                break
            except PolymarketApiError as exc:
                logger.exception("Erro de API no loop principal", extra={"event": "api_error", "error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                logger.exception("Erro inesperado no loop principal", extra={"event": "unexpected", "error": str(exc)})
            finally:
                time.sleep(self.settings.loop_interval_seconds)

    def list_open_orders(self, token_id: str | None = None) -> list[dict[str, Any]]:
        target_token_id = token_id or self.current_token_id
        open_orders = self._with_retry(lambda: self.client.get_open_orders(target_token_id))
        order_ids = self._extract_unique_order_ids(open_orders)
        self._remember_orders(open_orders)
        logger.info(
            "Open orders loaded",
            extra={"event": "open_orders", "token_id": target_token_id, "count": len(open_orders), "order_ids": order_ids},
        )
        return open_orders

    def list_all_open_orders(self) -> list[dict[str, Any]]:
        all_orders: list[dict[str, Any]] = []
        for token_id in self._relevant_token_ids():
            all_orders.extend(self.list_open_orders(token_id=token_id))
        return all_orders

    def cancel_open_orders(self, open_orders: list[dict[str, Any]]) -> dict[str, Any] | None:
        order_ids = self._extract_unique_order_ids(open_orders)
        if not order_ids:
            logger.info("Nenhuma ordem aberta para cancelar.", extra={"event": "cancel_skip"})
            return None

        cancel_resp = self._with_retry(lambda: self.client.cancel_orders(order_ids))
        for order_id in order_ids:
            self.known_orders.pop(order_id, None)
        logger.info("Canceled open orders", extra={"event": "cancel", "response": cancel_resp, "count": len(order_ids)})
        return cancel_resp

    def _with_retry(self, operation: Any) -> Any:
        attempt = 0
        while True:
            try:
                return operation()
            except PolymarketApiError:
                attempt += 1
                if attempt > self.settings.max_retries:
                    raise
                logger.warning(
                    "Retrying operation after API failure",
                    extra={
                        "event": "retry",
                        "attempt": attempt,
                        "max_retries": self.settings.max_retries,
                    },
                )
                time.sleep(self.settings.retry_delay_seconds)

    def _select_market(self) -> dict[str, Any]:
        if not self.settings.auto_select_market:
            market = self._with_retry(lambda: self.client.get_market_snapshot(self.current_token_id))
            logger.info(
                "Mercado mantido por configuracao",
                extra={"event": "market_selected", "token_id": market["token_id"], "liquidity_score": market["liquidity_score"]},
            )
            return market

        candidates = self.client.get_candidate_token_ids()
        best_market: dict[str, Any] | None = None
        for token_id in candidates:
            try:
                market = self._with_retry(lambda token_id=token_id: self.client.get_market_snapshot(token_id))
            except PolymarketApiError:
                continue

            if best_market is None or market["liquidity_score"] > best_market["liquidity_score"]:
                best_market = market

        if best_market is None:
            raise PolymarketApiError("Nao foi possivel selecionar mercado com liquidez.")

        logger.info(
            "Mercado selecionado por liquidez",
            extra={
                "event": "market_selected",
                "token_id": best_market["token_id"],
                "liquidity_score": best_market["liquidity_score"],
                "book_spread": best_market["book_spread"],
            },
        )
        return best_market

    def _determine_spread(self, market: dict[str, Any]) -> float:
        if not self.settings.dynamic_spread_enabled:
            return max(self.settings.spread, market["book_spread"])

        min_spread = min(self.settings.min_spread, self.settings.max_spread)
        max_spread = max(self.settings.min_spread, self.settings.max_spread)
        low = self.settings.liquidity_target_low
        high = max(self.settings.liquidity_target_high, low + 1e-9)
        liquidity = market["liquidity_score"]
        normalized = min(1.0, max(0.0, (liquidity - low) / (high - low)))
        adaptive_spread = max_spread - ((max_spread - min_spread) * normalized)
        final_spread = max(market["book_spread"], adaptive_spread)
        logger.info(
            "Spread dinamico calculado",
            extra={
                "event": "dynamic_spread",
                "token_id": market["token_id"],
                "liquidity_score": liquidity,
                "book_spread": market["book_spread"],
                "adaptive_spread": adaptive_spread,
                "final_spread": final_spread,
            },
        )
        return final_spread

    def _build_quotes(self, current_price: float, spread: float) -> dict[str, Quote]:
        half_spread = spread / 2
        buy_price = max(0.001, round(current_price - half_spread, 4))
        sell_price = min(0.999, round(current_price + half_spread, 4))
        return {
            "buy": Quote(side="buy", price=buy_price, size=self.settings.size),
            "sell": Quote(side="sell", price=sell_price, size=self.settings.size),
        }

    def _find_duplicate_target_orders(
        self, open_orders: list[dict[str, Any]], quotes: dict[str, Quote]
    ) -> dict[str, dict[str, Any]]:
        reusable: dict[str, dict[str, Any]] = {}

        for order in open_orders:
            side = self._extract_side(order)
            if side not in quotes:
                continue

            if self._matches_quote(order, quotes[side]) and side not in reusable:
                reusable[side] = order

        return reusable

    def _extract_unique_order_ids(self, open_orders: list[dict[str, Any]]) -> list[str]:
        order_ids: list[str] = []
        seen: set[str] = set()
        for order in open_orders:
            order_id = self.client.extract_order_id(order)
            if not order_id or order_id in seen:
                continue
            seen.add(order_id)
            order_ids.append(order_id)
        return order_ids

    def _relevant_token_ids(self) -> list[str]:
        token_ids = self.client.get_candidate_token_ids()
        token_ids.append(self.current_token_id)
        for snapshot in self.known_orders.values():
            token_id = snapshot.get("token_id")
            if token_id:
                token_ids.append(str(token_id))
        return list(dict.fromkeys(token_ids))

    def _check_fills(self) -> list[dict[str, Any]]:
        if not self.known_orders:
            return []

        still_open: dict[str, dict[str, Any]] = {}
        filled_orders: list[dict[str, Any]] = []
        for order_id, snapshot in self.known_orders.items():
            details = self._with_retry(lambda order_id=order_id: self.client.get_order_details(order_id))
            if not details:
                still_open[order_id] = snapshot
                continue

            status = self._extract_status(details)
            if status in FILLED_STATUSES:
                fill_info = {
                    "order_id": order_id,
                    "side": snapshot.get("side") or self._extract_side(details),
                    "price": self._extract_price(details) or snapshot.get("price"),
                    "size": self._extract_size(details) or snapshot.get("size"),
                    "status": status,
                    "details": details,
                }
                filled_orders.append(fill_info)
                logger.info("Ordem executada", extra={"event": "filled", **fill_info})
            elif status in CLOSED_STATUSES:
                logger.info(
                    "Ordem encerrada",
                    extra={"event": "closed", "order_id": order_id, "status": status},
                )
            else:
                still_open[order_id] = {**snapshot, **details}

        self.known_orders = still_open
        return filled_orders

    def _has_buy_balance(self, quote: Quote) -> bool:
        collateral = self._with_retry(self.client.get_collateral_balance)
        if collateral is None:
            logger.warning("Não foi possível validar collateral; prosseguindo com BUY.", extra={"event": "buy_balance_unknown"})
            return True

        required = (quote.price * quote.size) + self.settings.min_collateral_buffer
        logger.info(
            "Balance check BUY",
            extra={"event": "buy_balance", "collateral": collateral, "required": required},
        )
        return collateral >= required

    def _has_sell_balance(self, quote: Quote) -> bool:
        token_balance = self._with_retry(lambda: self.client.get_token_balance(self.current_token_id))
        if token_balance is None:
            logger.warning("Não foi possível validar posição do token; prosseguindo com SELL.", extra={"event": "sell_balance_unknown"})
            return True

        logger.info(
            "Balance check SELL",
            extra={"event": "sell_balance", "token_balance": token_balance, "required": quote.size},
        )
        return token_balance >= quote.size

    def _get_token_balance(self) -> float | None:
        return self._with_retry(lambda: self.client.get_token_balance(self.current_token_id))

    def _position_allows_side(self, side: str, token_balance: float | None) -> bool:
        if token_balance is None:
            logger.warning(
                "Nao foi possivel validar posicao; prosseguindo com ambos os lados.",
                extra={"event": "position_unknown"},
            )
            return True

        lower_bound = max(0.0, self.settings.target_position_size - self.settings.max_position_imbalance)
        upper_bound = self.settings.target_position_size + self.settings.max_position_imbalance
        logger.info(
            "Position check",
            extra={
                "event": "position_check",
                "side": side,
                "token_balance": token_balance,
                "target_position": self.settings.target_position_size,
                "lower_bound": lower_bound,
                "upper_bound": upper_bound,
            },
        )

        if side == "buy":
            return token_balance < upper_bound
        if side == "sell":
            return token_balance > lower_bound
        return True

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
            self.known_orders[order_id] = self._build_order_snapshot(response)

    def _remember_orders(self, orders: list[dict[str, Any]]) -> None:
        for order in orders:
            order_id = self.client.extract_order_id(order)
            if not order_id:
                continue
            self.known_orders[order_id] = self._build_order_snapshot(order)

    def _build_order_snapshot(self, order: dict[str, Any]) -> dict[str, Any]:
        return {
            "token_id": str(order.get("token_id") or order.get("market") or self.current_token_id),
            "side": self._extract_side(order),
            "price": self._extract_price(order),
            "size": self._extract_size(order),
            "status": self._extract_status(order),
            "raw": order,
        }
