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
        self.position_size: float = state.position_size
        self.average_entry_price: float = state.average_entry_price
        self.realized_pnl: float = state.realized_pnl
        self.trades_executed: int = state.trades_executed
        self.cancel_timestamps: list[float] = state.cancel_timestamps or []
        self.paused_until: float = state.paused_until
        self.consecutive_api_errors: int = state.consecutive_api_errors
        self.last_api_error_time: float = state.last_api_error_time
        self.last_api_sync_time: float = state.last_api_sync_time
        self.cycles_without_fill: int = state.cycles_without_fill
        self.gross_bought: float = state.gross_bought
        self.gross_sold: float = state.gross_sold
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
                self._pause_after_error_streak("api_errors", self.consecutive_api_errors)
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
                self._pause_after_error_streak("execution_errors", self.consecutive_execution_errors)
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
        trades_before_cycle = self.trades_executed
        self._sync_orders_with_api()
        self._validate_state_or_raise()
        collateral, token_balance = self._get_confirmed_balances()
        market = self._get_valid_market_snapshot()
        if not market:
            logger.info(
                "Cycle skipped due to market conditions",
                extra={"event": "cycle_skip_market_conditions", "token_id": self.settings.token_id},
            )
            self._update_protection_state(trades_before_cycle)
            return
        quotes = self._build_quotes(market["midpoint"], market["spread_multiplier"])
        if not self._quotes_are_profitable(quotes, market["midpoint"]):
            self._log_non_operation(
                "spread_not_profitable",
                estimated_cost=round(self._estimate_round_trip_cost(market["midpoint"]), 6),
                required_spread=round(self._required_profit_spread(market["midpoint"]), 6),
                spread=round(quotes["sell"].price - quotes["buy"].price, 6),
            )
            self._update_protection_state(trades_before_cycle)
            return

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
        self._update_protection_state(trades_before_cycle)
        self._log_result_snapshot("cycle_result")
        logger.info(
            "Cycle completed successfully",
            extra={"event": "cycle_success", "token_id": self.settings.token_id, "open_orders": len(self.known_orders)},
        )

    def list_open_orders(self) -> list[dict[str, Any]]:
        orders = self._with_retry(lambda: self.client.get_open_orders(self.settings.token_id))
        self.known_orders = self._snapshots_by_id(orders, self.known_orders)
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
        self._record_cancellation()
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
        previous_orders = dict(self.known_orders)
        current_order_ids = {
            self.client.extract_order_id(order)
            for order in orders
            if self.client.extract_order_id(order)
        }
        self._process_closed_orders(previous_orders, current_order_ids)
        self.known_orders = self._snapshots_by_id(orders, previous_orders)
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
        if self.settings.dry_run:
            # In dry run mode, use placeholder values
            logger.info(
                "Dry run mode: using placeholder balances",
                extra={"event": "dry_run_balances", "token_id": self.settings.token_id},
            )
            return (1000.0, 1000.0)
        
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
        liquidity_score = self._calculate_liquidity_score(market)
        volatility_ratio = self._calculate_volatility_ratio(midpoint)

        if spread < self.settings.min_operable_spread:
            self._log_non_operation("spread_below_threshold", book_spread=spread)
            return {}
        if spread > self.settings.max_operable_spread:
            self._log_non_operation("spread_above_threshold", book_spread=spread)
            return {}
        if liquidity_score < self.settings.min_liquidity_score:
            self._log_non_operation("low_liquidity", liquidity_score=round(liquidity_score, 4))
            return {}
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
        if volatility_ratio >= self.settings.max_volatility_ratio:
            self.paused_until = max(self.paused_until, time.time() + self.settings.protection_pause_seconds)
            self._log_non_operation(
                "high_volatility_pause",
                volatility_ratio=round(volatility_ratio, 6),
                paused_until=round(self.paused_until, 3),
            )
            return {}

        spread_multiplier = self._determine_spread_multiplier(volatility_ratio)
        logger.info(
            "Market snapshot validated",
            extra={
                "event": "market_snapshot",
                "token_id": self.settings.token_id,
                "price": midpoint,
                "previous_price": self.last_market_price,
                "book_spread": spread,
                "liquidity_score": round(liquidity_score, 4),
                "volatility_ratio": round(volatility_ratio, 6),
                "spread_multiplier": spread_multiplier,
            },
        )
        market["liquidity_score"] = liquidity_score
        market["volatility_ratio"] = volatility_ratio
        market["spread_multiplier"] = spread_multiplier
        return market

    def _build_quotes(self, midpoint: float, spread_multiplier: float = 1.0) -> dict[str, Quote]:
        half_spread = (self.settings.spread * spread_multiplier) / 2.0
        buy_price = round(max(self.settings.min_price_bound, midpoint - half_spread), 4)
        sell_price = round(min(self.settings.max_price_bound, midpoint + half_spread), 4)
        buy_price, sell_price = self._apply_inventory_skew(buy_price, sell_price)
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
                "spread": round(sell_price - buy_price, 6),
                "spread_multiplier": spread_multiplier,
                "net_position": round(self._net_position(), 4),
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
        now = time.time()
        for order in open_orders:
            first_seen_at = float(order.get("first_seen_at") or now)
            if now - first_seen_at > self.settings.max_order_age_seconds:
                logger.info(
                    "Refreshing stale order by timeout",
                    extra={
                        "event": "refresh_order_timeout",
                        "token_id": self.settings.token_id,
                        "order_id": self.client.extract_order_id(order),
                        "age_seconds": round(now - first_seen_at, 1),
                    },
                )
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
        if self._placement_paused():
            logger.warning(
                "Order placement paused after excessive churn",
                extra={"event": "placement_paused", "token_id": self.settings.token_id, "paused_until": self.paused_until},
            )
            return
        protection_mode = self.cycles_without_fill >= self.settings.protection_no_fill_cycles
        max_orders_this_cycle = 1 if protection_mode else self.settings.max_orders_per_cycle
        if protection_mode:
            logger.warning(
                "Protection mode reducing activity after repeated idle cycles",
                extra={
                    "event": "protection_reduce_activity",
                    "token_id": self.settings.token_id,
                    "cycles_without_fill": self.cycles_without_fill,
                    "count": max_orders_this_cycle,
                },
            )
        placed_count = 0
        for side in ("buy", "sell"):
            if placed_count >= max_orders_this_cycle:
                logger.warning(
                    "Max orders per cycle reached",
                    extra={"event": "max_orders_per_cycle_reached", "token_id": self.settings.token_id, "count": placed_count},
                )
                break
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
            placed_count += 1
            if not self.settings.dry_run:
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

    def _snapshots_by_id(self, orders: list[dict[str, Any]], previous_orders: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        snapshots: dict[str, dict[str, Any]] = {}
        now = time.time()
        for order in orders:
            order_id = self.client.extract_order_id(order)
            if not order_id:
                raise PolymarketApiError("Open order returned by API without id")
            previous_snapshot = previous_orders.get(order_id, {})
            snapshots[order_id] = {
                "token_id": self.settings.token_id,
                "side": self.client.extract_side(order),
                "price": self.client.extract_price(order),
                "size": self.client.extract_size(order),
                "status": self.client.extract_status(order),
                "first_seen_at": float(previous_snapshot.get("first_seen_at") or now),
                "raw": order,
            }
        return snapshots

    def _save_state(self) -> None:
        self.state_store.save(
            BotState(
                known_orders=self.known_orders,
                last_market_price=self.last_market_price,
                position_size=self.position_size,
                average_entry_price=self.average_entry_price,
                realized_pnl=self.realized_pnl,
                trades_executed=self.trades_executed,
                cancel_timestamps=self.cancel_timestamps,
                paused_until=self.paused_until,
                consecutive_api_errors=self.consecutive_api_errors,
                last_api_error_time=self.last_api_error_time,
                last_api_sync_time=self.last_api_sync_time,
                cycles_without_fill=self.cycles_without_fill,
                gross_bought=self.gross_bought,
                gross_sold=self.gross_sold,
            )
        )

    def _check_kill_switch(self) -> bool:
        return Path(self.settings.kill_switch_flag_file).exists()

    def _process_closed_orders(self, previous_orders: dict[str, dict[str, Any]], current_order_ids: set[str]) -> None:
        for order_id, snapshot in previous_orders.items():
            if order_id in current_order_ids:
                continue
            details = self._with_retry(lambda order_id=order_id: self.client.get_order_details(order_id))
            if not details:
                logger.warning(
                    "Order disappeared without details",
                    extra={"event": "closed_order_missing_details", "token_id": self.settings.token_id, "order_id": order_id},
                )
                continue
            status = self.client.extract_status(details)
            filled_size = self.client.extract_filled_size(details) or self.client.extract_size(details) or snapshot.get("size") or 0.0
            if status in {"filled", "matched", "executed", "complete", "completed"} and filled_size > 0:
                self._apply_fill(
                    side=snapshot.get("side") or self.client.extract_side(details),
                    price=float(self.client.extract_price(details) or snapshot.get("price") or 0.0),
                    size=float(filled_size),
                    order_id=order_id,
                )

    def _apply_fill(self, side: str, price: float, size: float, order_id: str) -> None:
        if size <= 0 or price <= 0:
            return
        realized_delta = 0.0
        if side == "buy":
            total_cost = (self.average_entry_price * self.position_size) + (price * size)
            self.position_size += size
            self.average_entry_price = total_cost / self.position_size if self.position_size > 0 else 0.0
            self.gross_bought += price * size
        elif side == "sell":
            matched_size = min(self.position_size, size)
            realized_delta = (price - self.average_entry_price) * matched_size
            self.realized_pnl += realized_delta
            self.position_size = max(0.0, self.position_size - matched_size)
            self.gross_sold += price * size
            if self.position_size <= self.settings.price_tolerance:
                self.position_size = 0.0
                self.average_entry_price = 0.0
        self.cycles_without_fill = 0
        self.trades_executed += 1
        logger.info(
            "Fill processed",
            extra={
                "event": "fill_processed",
                "token_id": self.settings.token_id,
                "order_id": order_id,
                "side": side,
                "price": price,
                "size": size,
                "position_size": round(self.position_size, 4),
                "realized_pnl": round(self.realized_pnl, 4),
                "realized_delta": round(realized_delta, 4),
                "gross_bought": round(self.gross_bought, 4),
                "gross_sold": round(self.gross_sold, 4),
            },
        )
        self._log_result_snapshot("fill_result")

    def _record_cancellation(self) -> None:
        now = time.time()
        one_minute_ago = now - 60.0
        self.cancel_timestamps = [ts for ts in self.cancel_timestamps if ts >= one_minute_ago]
        self.cancel_timestamps.append(now)
        if len(self.cancel_timestamps) >= self.settings.max_cancels_per_minute:
            self.paused_until = now + self.settings.pause_after_cancel_limit_seconds
            logger.warning(
                "Cancellation churn limit reached, pausing placement",
                extra={
                    "event": "cancel_churn_pause",
                    "token_id": self.settings.token_id,
                    "cancel_count": len(self.cancel_timestamps),
                    "paused_until": self.paused_until,
                },
            )

    def _placement_paused(self) -> bool:
        return time.time() < self.paused_until

    def _calculate_liquidity_score(self, market: dict[str, Any]) -> float:
        raw_book = market.get("raw_book") or {}
        bids = raw_book.get("bids") or []
        asks = raw_book.get("asks") or []
        if not bids and not asks:
            return self.settings.min_liquidity_score
        return self._sum_level_sizes(bids[:3]) + self._sum_level_sizes(asks[:3])

    def _sum_level_sizes(self, levels: list[Any]) -> float:
        total = 0.0
        for level in levels:
            if isinstance(level, dict):
                value = level.get("size") or level.get("quantity")
            else:
                value = getattr(level, "size", None)
                if value is None:
                    value = getattr(level, "quantity", None)
            if value is not None:
                total += float(value)
        return total

    def _calculate_volatility_ratio(self, midpoint: float) -> float:
        if self.last_market_price in {None, 0}:
            return 0.0
        return abs(midpoint - float(self.last_market_price)) / abs(float(self.last_market_price))

    def _determine_spread_multiplier(self, volatility_ratio: float) -> float:
        multiplier = 1.0
        if self.cycles_without_fill >= self.settings.protection_no_fill_cycles:
            multiplier = max(multiplier, self.settings.protection_spread_multiplier)
        if volatility_ratio >= self.settings.max_volatility_ratio * 0.5:
            multiplier = max(multiplier, self.settings.protection_spread_multiplier)
        return multiplier

    def _apply_inventory_skew(self, buy_price: float, sell_price: float) -> tuple[float, float]:
        soft_limit = max(self.settings.inventory_soft_limit, self.settings.price_tolerance)
        imbalance = max(-1.0, min(1.0, self._net_position() / soft_limit))
        adjustment = round(self.settings.inventory_price_adjustment * abs(imbalance), 4)
        tick_size = max(float(self.settings.tick_size), self.settings.price_tolerance)

        if imbalance > 0:
            sell_price = max(round(buy_price + tick_size, 4), round(sell_price - adjustment, 4))
        elif imbalance < 0:
            buy_price = min(round(sell_price - tick_size, 4), round(buy_price + adjustment, 4))
        return buy_price, sell_price

    def _net_position(self) -> float:
        return self.position_size - self.settings.inventory_target

    def _estimate_round_trip_cost(self, midpoint: float) -> float:
        return midpoint * ((2 * self.settings.fee_rate) + self.settings.estimated_slippage_rate) + self.settings.min_profit_margin

    def _required_profit_spread(self, midpoint: float) -> float:
        return self._estimate_round_trip_cost(midpoint)

    def _quotes_are_profitable(self, quotes: dict[str, Quote], midpoint: float) -> bool:
        spread = quotes["sell"].price - quotes["buy"].price
        return spread >= self._required_profit_spread(midpoint)

    def _update_protection_state(self, trades_before_cycle: int) -> None:
        if self.trades_executed > trades_before_cycle:
            self.cycles_without_fill = 0
            return
        self.cycles_without_fill += 1

    def _pause_after_error_streak(self, reason: str, streak: int) -> None:
        if streak < self.settings.protection_error_streak:
            return
        self.paused_until = max(self.paused_until, time.time() + self.settings.protection_pause_seconds)
        self._log_non_operation(reason, paused_until=round(self.paused_until, 3))

    def _log_non_operation(self, reason: str, **extra: Any) -> None:
        logger.warning(
            "Strategy decided not to operate",
            extra={"event": "non_operable_market", "token_id": self.settings.token_id, "reason": reason, **extra},
        )

    def _log_result_snapshot(self, event: str) -> None:
        logger.info(
            "Result snapshot",
            extra={
                "event": event,
                "token_id": self.settings.token_id,
                "gross_bought": round(self.gross_bought, 4),
                "gross_sold": round(self.gross_sold, 4),
                "position_size": round(self.position_size, 4),
                "realized_pnl": round(self.realized_pnl, 4),
            },
        )
