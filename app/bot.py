from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import Settings
from app.polymarket import PolymarketApiError, PolymarketClient
from app.state import BotState, BotStateStore

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
        self.state_store = BotStateStore(settings.state_file)
        state = self.state_store.load()
        self.known_orders: dict[str, dict[str, Any]] = state.known_orders
        self.last_market_price: float | None = state.last_market_price
        self.consecutive_api_errors: int = state.consecutive_api_errors
        self.last_api_error_time: float = state.last_api_error_time
        self.last_api_sync_time: float = state.last_api_sync_time
        self.consecutive_execution_errors: int = 0
        self.shutdown_event = threading.Event()

    def request_shutdown(self, reason: str = "external_signal") -> None:
        logger.info(
            "Shutdown requested",
            extra={"event": "shutdown_requested", "token_id": self.settings.token_id, "reason": reason},
        )
        self.shutdown_event.set()

    def run_forever(self) -> None:
        logger.info(
            "Starting market maker bot",
            extra={"event": "startup", "token_id": self.settings.token_id, "interval_seconds": self.settings.loop_interval_seconds},
        )
        while not self.shutdown_event.is_set():
            if self._check_kill_switch():
                logger.critical("Kill switch activated", extra={"event": "kill_switch"})
                self.shutdown_event.set()
                break

            try:
                self.run_once()
                self.consecutive_api_errors = 0
                self.consecutive_execution_errors = 0
                self.last_api_error_time = 0.0
            except KeyboardInterrupt:
                logger.info("Bot interrupted by user", extra={"event": "shutdown"})
                self.shutdown_event.set()
                break
            except PolymarketApiError as exc:
                self.consecutive_api_errors += 1
                self.last_api_error_time = time.time()
                logger.exception(
                    "Critical API error in main loop",
                    extra={
                        "event": "api_error",
                        "error": str(exc),
                        "consecutive_errors": self.consecutive_api_errors,
                        "limit": self.settings.max_api_failure_streak,
                    },
                )
                if self.consecutive_api_errors >= self.settings.max_api_failure_streak:
                    logger.critical("Stopping bot after repeated API failures", extra={"event": "api_failure_stop"})
                    self.shutdown_event.set()
            except Exception as exc:  # noqa: BLE001
                self.consecutive_execution_errors += 1
                logger.exception(
                    "Unexpected execution error",
                    extra={
                        "event": "unexpected_error",
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                        "consecutive_errors": self.consecutive_execution_errors,
                        "limit": self.settings.max_consecutive_errors,
                    },
                )
                if self.consecutive_execution_errors >= self.settings.max_consecutive_errors:
                    logger.critical("Stopping bot after repeated execution failures", extra={"event": "execution_failure_stop"})
                    self.shutdown_event.set()
            finally:
                self._save_state()
                if not self.shutdown_event.is_set():
                    time.sleep(self.settings.loop_interval_seconds)

    def run_once(self) -> None:
        self._sync_orders_with_api()
        self._validate_state_or_raise()
        collateral, token_balance = self._get_confirmed_balances()
        market = self._get_valid_market_snapshot()
        quotes = self._build_quotes(market["midpoint"])

        self._ensure_fresh_sync()
        open_orders = list(self.known_orders.values())

        if self._should_refresh_orders(open_orders, quotes, market["midpoint"]):
            self.cancel_open_orders(open_orders)
            self._sync_orders_with_api()
            self._validate_state_or_raise()
            collateral, token_balance = self._get_confirmed_balances()
            open_orders = list(self.known_orders.values())

        self._place_missing_quotes(quotes, collateral, token_balance, open_orders)
        self.last_market_price = float(market["midpoint"])
        logger.info(
            "Cycle completed successfully",
            extra={"event": "cycle_success", "token_id": self.settings.token_id, "open_orders": len(self.known_orders)},
        )

    def list_open_orders(self) -> list[dict[str, Any]]:
        orders = self._with_retry(lambda: self.client.get_open_orders(self.settings.token_id))
        self.known_orders = self._snapshots_by_id(orders)
        logger.debug(
            "Open orders loaded from API",
            extra={"event": "open_orders", "token_id": self.settings.token_id, "count": len(orders)},
        )
        return orders

    def cancel_open_orders(self, open_orders: list[dict[str, Any]]) -> None:
        order_ids = [order_id for order_id in (self.client.extract_order_id(order) for order in open_orders) if order_id]
        if not order_ids:
            logger.info("No open orders to cancel", extra={"event": "cancel_skip", "token_id": self.settings.token_id})
            return

        logger.info(
            "Cancelling existing orders",
            extra={"event": "cancel_start", "token_id": self.settings.token_id, "count": len(order_ids)},
        )
        if self.settings.dry_run:
            logger.warning(
                "DRY_RUN enabled, skipping cancellation",
                extra={"event": "dry_run_cancel_skip", "token_id": self.settings.token_id, "count": len(order_ids)},
            )
            return
        self._with_retry(lambda: self.client.cancel_orders(order_ids))
        self._sync_orders_with_api()
        logger.info(
            "Orders cancelled successfully",
            extra={"event": "cancel_success", "token_id": self.settings.token_id, "count": len(order_ids)},
        )

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
                    "Retrying API call",
                    extra={"event": "retry", "attempt": attempt, "max_retries": self.settings.max_retries},
                )
                time.sleep(self.settings.retry_delay_seconds)

    def _sync_orders_with_api(self) -> None:
        orders = self._with_retry(lambda: self.client.get_open_orders(self.settings.token_id))
        self.known_orders = self._snapshots_by_id(orders)
        self.last_api_sync_time = time.time()
        logger.debug(
            "API sync complete",
            extra={"event": "api_sync_complete", "token_id": self.settings.token_id, "count": len(self.known_orders), "sync_timestamp": self.last_api_sync_time},
        )

    def _validate_state_or_raise(self) -> None:
        seen_sides: set[str] = set()
        for order_id, snapshot in self.known_orders.items():
            if snapshot.get("token_id") != self.settings.token_id:
                raise PolymarketApiError(f"Unexpected token in local state for order {order_id}")

            side = snapshot.get("side")
            price = snapshot.get("price")
            size = snapshot.get("size")
            if side not in {"buy", "sell"} or price is None or size is None:
                raise PolymarketApiError(f"Incomplete order snapshot for order {order_id}")
            if float(price) <= 0 or float(size) <= 0:
                raise PolymarketApiError(f"Invalid order snapshot values for order {order_id}")
            if side in seen_sides:
                raise PolymarketApiError(f"Duplicate side detected in open orders: {side}")
            seen_sides.add(side)

    def _get_confirmed_balances(self) -> tuple[float, float]:
        collateral = self._with_retry(self.client.get_collateral_balance)
        token_balance = self._with_retry(lambda: self.client.get_token_balance(self.settings.token_id))
        if collateral is None or token_balance is None:
            raise PolymarketApiError("Unable to confirm balances from API")
        if collateral < self.settings.min_balance_threshold:
            raise PolymarketApiError("Collateral below minimum safety threshold")
        logger.info(
            "Balances confirmed",
            extra={
                "event": "balances_confirmed",
                "token_id": self.settings.token_id,
                "collateral": collateral,
                "token_balance": token_balance,
            },
        )
        return float(collateral), float(token_balance)

    def _get_valid_market_snapshot(self) -> dict[str, Any]:
        market = self._with_retry(lambda: self.client.get_market_snapshot(self.settings.token_id))
        midpoint = float(market["midpoint"])
        spread = float(market["book_spread"])

        if midpoint < self.settings.min_price_bound or midpoint > self.settings.max_price_bound:
            raise PolymarketApiError(f"Midpoint outside configured bounds: {midpoint}")
        if midpoint <= 0:
            raise PolymarketApiError("Midpoint must be positive")

        max_spread = midpoint * self.settings.max_book_spread_pct
        if spread > max_spread:
            raise PolymarketApiError(f"Book spread too wide: {spread}")

        if self.last_market_price not in {None, 0}:
            deviation_ratio = abs(midpoint - float(self.last_market_price)) / abs(float(self.last_market_price))
            if deviation_ratio > self.settings.max_midpoint_deviation_ratio:
                raise PolymarketApiError(f"Midpoint deviation too large: {deviation_ratio}")

        logger.info(
            "Market snapshot validated",
            extra={
                "event": "market_snapshot",
                "token_id": self.settings.token_id,
                "price": midpoint,
                "previous_price": self.last_market_price,
                "book_spread": spread,
            },
        )
        return market

    def _build_quotes(self, midpoint: float) -> dict[str, Quote]:
        half_spread = self.settings.spread / 2.0
        buy_price = round(max(self.settings.min_price_bound, midpoint - half_spread), 4)
        sell_price = round(min(self.settings.max_price_bound, midpoint + half_spread), 4)
        quotes = {
            "buy": Quote(side="buy", price=buy_price, size=self.settings.size),
            "sell": Quote(side="sell", price=sell_price, size=self.settings.size),
        }
        logger.info(
            "Quotes prepared",
            extra={
                "event": "quotes",
                "token_id": self.settings.token_id,
                "buy_price": buy_price,
                "sell_price": sell_price,
                "size": self.settings.size,
                "spread": self.settings.spread,
            },
        )
        return quotes

    def _ensure_fresh_sync(self) -> None:
        if time.time() - self.last_api_sync_time <= self.settings.max_sync_age_seconds:
            return
        logger.warning("API sync is stale, refreshing", extra={"event": "sync_stale", "token_id": self.settings.token_id})
        self._sync_orders_with_api()

    def _should_refresh_orders(self, open_orders: list[dict[str, Any]], quotes: dict[str, Quote], midpoint: float) -> bool:
        if not open_orders:
            return False
        if len(open_orders) > 2:
            logger.warning("Too many open orders for fixed strategy", extra={"event": "refresh_too_many_orders", "count": len(open_orders)})
            return True

        price_move_ratio = 0.0
        if self.last_market_price not in {None, 0}:
            price_move_ratio = abs(midpoint - float(self.last_market_price)) / abs(float(self.last_market_price))
        if price_move_ratio >= self.settings.reposition_price_threshold:
            logger.info(
                "Refreshing orders after price move",
                extra={"event": "refresh_price_move", "token_id": self.settings.token_id, "price_change_ratio": round(price_move_ratio, 6)},
            )
            return True

        side_counts = {"buy": 0, "sell": 0}
        for order in open_orders:
            side = self.client.extract_side(order)
            if side not in side_counts:
                logger.warning("Unknown order side detected", extra={"event": "refresh_unknown_side"})
                return True
            side_counts[side] += 1
            if not self._matches_quote(order, quotes[side]):
                logger.info(
                    "Refreshing orders after quote mismatch",
                    extra={"event": "refresh_quote_mismatch", "token_id": self.settings.token_id, "side": side},
                )
                return True

        if side_counts["buy"] > 1 or side_counts["sell"] > 1:
            logger.warning("Duplicate orders detected", extra={"event": "refresh_duplicate_orders", "token_id": self.settings.token_id})
            return True
        return False

    def _place_missing_quotes(
        self,
        quotes: dict[str, Quote],
        collateral: float,
        token_balance: float,
        open_orders: list[dict[str, Any]],
    ) -> None:
        for side in ("buy", "sell"):
            quote = quotes[side]
            if self._has_matching_order(open_orders, quote):
                logger.debug(
                    "Matching order already exists",
                    extra={"event": "place_skip_existing", "token_id": self.settings.token_id, "side": side, "price": quote.price},
                )
                continue

            if side == "buy":
                required = (quote.price * quote.size) + self.settings.min_collateral_buffer
                available = collateral * self.settings.max_balance_usage_pct
                if available < required:
                    logger.warning(
                        "Skipping buy due to insufficient confirmed collateral",
                        extra={"event": "place_skip_buy_balance", "token_id": self.settings.token_id, "required": required, "collateral": collateral},
                    )
                    continue
            else:
                required = quote.size
                available = token_balance * self.settings.max_balance_usage_pct
                if available < required:
                    logger.warning(
                        "Skipping sell due to insufficient confirmed token balance",
                        extra={"event": "place_skip_sell_balance", "token_id": self.settings.token_id, "required": required, "token_balance": token_balance},
                    )
                    continue

            self._place_order(quote)
            self._sync_orders_with_api()
            open_orders = list(self.known_orders.values())

    def _place_order(self, quote: Quote) -> None:
        if not self._quote_is_complete(quote):
            raise PolymarketApiError("Attempted to place incomplete quote")
        if self.settings.dry_run:
            logger.warning(
                "DRY_RUN enabled, order not sent",
                extra={
                    "event": "dry_run_place_order",
                    "token_id": self.settings.token_id,
                    "side": quote.side,
                    "price": quote.price,
                    "size": quote.size,
                },
            )
            return
        response = self._with_retry(
            lambda: self.client.place_limit_order(
                token_id=self.settings.token_id,
                side=quote.side,
                price=quote.price,
                size=quote.size,
            )
        )
        order_id = self.client.extract_order_id(response)
        if not order_id:
            raise PolymarketApiError("Order placement returned no order id")
        logger.info(
            "Order created",
            extra={
                "event": "place_order",
                "token_id": self.settings.token_id,
                "side": quote.side,
                "price": quote.price,
                "size": quote.size,
                "order_id": order_id,
            },
        )

    def _has_matching_order(self, open_orders: list[dict[str, Any]], quote: Quote) -> bool:
        return any(self._matches_quote(order, quote) for order in open_orders if self.client.extract_side(order) == quote.side)

    def _matches_quote(self, order: dict[str, Any], quote: Quote) -> bool:
        price = self.client.extract_price(order)
        size = self.client.extract_size(order)
        if price is None or size is None:
            return False
        return abs(price - quote.price) <= self.settings.price_tolerance and abs(size - quote.size) <= self.settings.price_tolerance

    @staticmethod
    def _quote_is_complete(quote: Quote) -> bool:
        return quote.side in {"buy", "sell"} and quote.price > 0 and quote.size > 0

    def _snapshots_by_id(self, orders: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        snapshots: dict[str, dict[str, Any]] = {}
        for order in orders:
            order_id = self.client.extract_order_id(order)
            if not order_id:
                raise PolymarketApiError("Open order returned by API without id")
            snapshots[order_id] = {
                "token_id": self.settings.token_id,
                "side": self.client.extract_side(order),
                "price": self.client.extract_price(order),
                "size": self.client.extract_size(order),
                "status": self.client.extract_status(order),
                "raw": order,
            }
        return snapshots

    def _save_state(self) -> None:
        self.state_store.save(
            BotState(
                known_orders=self.known_orders,
                last_market_price=self.last_market_price,
                consecutive_api_errors=self.consecutive_api_errors,
                last_api_error_time=self.last_api_error_time,
                last_api_sync_time=self.last_api_sync_time,
            )
        )

    def _check_kill_switch(self) -> bool:
        return Path(self.settings.kill_switch_flag_file).exists()
