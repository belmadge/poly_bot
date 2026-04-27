from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import Settings
from app.polymarket import CLOSED_STATUSES, FILLED_STATUSES, PolymarketApiError, PolymarketClient
from app.state import BotState, BotStateStore


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Quote:
    side: str
    price: float
    size: float


@dataclass(frozen=True)
class QuoteContext:
    token_id: str
    current_price: float
    previous_price: float | None
    price_change_ratio: float
    spread: float
    book_spread: float
    liquidity_score: float
    quotes: dict[str, Quote]


@dataclass(frozen=True)
class ExecutionPlan:
    buy: Quote
    sell: Quote
    net_position: float


class MarketMakerBot:
    def __init__(self, settings: Settings, client: PolymarketClient) -> None:
        self.settings = settings
        self.client = client
        self.state_store = BotStateStore(settings.state_file)
        state = self.state_store.load()
        self.known_orders: dict[str, dict[str, Any]] = state.known_orders
        self.net_positions: dict[str, float] = state.net_positions
        self.last_market_prices: dict[str, float] = state.last_market_prices
        self.current_token_id = settings.token_id
        # Performance metrics
        self.trades_executed: int = state.trades_executed
        self.total_pnl_realized: float = state.total_pnl_realized
        self.last_metrics_log_time: float = state.last_metrics_log_time
        # Error tracking
        self.consecutive_api_errors: int = state.consecutive_api_errors
        self.last_api_error_time: float = state.last_api_error_time
        self.consecutive_execution_errors: int = 0
        # Last executed fill timestamp for order synchronization
        self.last_api_sync_time: float = time.time()

    def run_once(self) -> None:
        # CRITICAL: Synchronize with API - ensure API is source of truth
        self._sync_orders_with_api()
        
        # Check balance thresholds
        if not self._check_balance_safety():
            logger.critical("KILL SWITCH: Balance below minimum threshold", extra={"event": "kill_switch_balance"})
            self._save_state()
            raise PolymarketApiError("Balance safety check failed - stopping bot")
        
        context = self._build_quote_context()
        filled_orders = self._check_fills()
        token_balance = self._get_token_balance()
        execution_plan = self._build_execution_plan(context)

        if filled_orders:
            logger.info(
                "Posicao apos execucao",
                extra={
                    "event": "position_after_fill",
                    "token_id": context.token_id,
                    "token_balance": token_balance,
                    "filled_count": len(filled_orders),
                },
            )

        # SMART CANCELLATION: Only cancel orders if needed (price moved significantly or orders out of sync)
        self._cancel_orders_for_inactive_markets(context.token_id)
        current_open_orders = self.list_open_orders(token_id=context.token_id)
        
        # Check if reposition is needed (REPOSITIONING CONTROL)
        if self._should_refresh_orders_smart(context, current_open_orders):
            logger.info(
                "Cancelando ordens antigas para reposicionamento",
                extra={
                    "event": "cancel_for_reposition",
                    "token_id": context.token_id,
                    "count": len(current_open_orders),
                },
            )
            self.cancel_open_orders(current_open_orders)
            remaining_open_orders = self.list_open_orders(token_id=context.token_id)
        else:
            remaining_open_orders = current_open_orders
            
        self._place_quotes(context, execution_plan, token_balance, remaining_open_orders)
        self.last_market_prices[context.token_id] = context.current_price
        
        # Log performance metrics periodically
        self._log_performance_metrics()
        
        self._save_state()

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
            # Check kill switch
            if self._check_kill_switch():
                logger.critical("KILL SWITCH ACTIVATED. Shutting down bot.", extra={"event": "kill_switch"})
                break
                
            try:
                self.run_once()
                # Reset error counters on success
                self.consecutive_api_errors = 0
                self.consecutive_execution_errors = 0
                self.last_api_error_time = 0.0
            except KeyboardInterrupt:
                logger.info("Bot interrompido pelo usuario.", extra={"event": "shutdown"})
                break
            except PolymarketApiError as exc:
                self.consecutive_api_errors += 1
                self.last_api_error_time = time.time()
                logger.exception(
                    "Erro de API no loop principal",
                    extra={
                        "event": "api_error",
                        "error": str(exc),
                        "consecutive_errors": self.consecutive_api_errors,
                        "max_allowed": self.settings.max_api_failure_streak,
                    },
                )
                # Check if we've exceeded max API failures
                if self.consecutive_api_errors >= self.settings.max_api_failure_streak:
                    logger.critical(
                        "KILL SWITCH: Max API failures exceeded",
                        extra={
                            "event": "kill_switch_api_failures",
                            "consecutive_errors": self.consecutive_api_errors,
                        },
                    )
                    self._save_state()
                    break
            except Exception as exc:  # noqa: BLE001
                self.consecutive_execution_errors += 1
                logger.exception(
                    "Erro inesperado no loop principal",
                    extra={
                        "event": "unexpected",
                        "error": str(exc),
                        "consecutive_errors": self.consecutive_execution_errors,
                    },
                )
                # Check if we've exceeded max execution errors
                if self.consecutive_execution_errors >= self.settings.max_consecutive_errors:
                    logger.critical(
                        "KILL SWITCH: Max execution errors exceeded",
                        extra={
                            "event": "kill_switch_exec_errors",
                            "consecutive_errors": self.consecutive_execution_errors,
                        },
                    )
                    self._save_state()
                    break
            finally:
                self._save_state()
                time.sleep(self.settings.loop_interval_seconds)

    def list_open_orders(self, token_id: str | None = None) -> list[dict[str, Any]]:
        target_token_id = token_id or self.current_token_id
        open_orders = self._with_retry(lambda: self.client.get_open_orders(target_token_id))
        self._remember_orders(open_orders)
        logger.info(
            "Ordens abertas listadas",
            extra={
                "event": "open_orders",
                "token_id": target_token_id,
                "count": len(open_orders),
                "order_ids": self._extract_unique_order_ids(open_orders),
            },
        )
        return open_orders

    def list_all_open_orders(self) -> list[dict[str, Any]]:
        all_orders: list[dict[str, Any]] = []
        for token_id in self._relevant_token_ids():
            all_orders.extend(self.list_open_orders(token_id=token_id))
        return all_orders

    def cancel_all_open_orders(self) -> dict[str, Any] | None:
        return self.cancel_open_orders(self.list_all_open_orders())

    def _cancel_orders_for_inactive_markets(self, active_token_id: str) -> None:
        for token_id in self._relevant_token_ids():
            if token_id == active_token_id:
                continue
            open_orders = self.list_open_orders(token_id=token_id)
            if open_orders:
                self.cancel_open_orders(open_orders)

    def cancel_open_orders(self, open_orders: list[dict[str, Any]]) -> dict[str, Any] | None:
        order_ids = self._extract_unique_order_ids(open_orders)
        if not order_ids:
            logger.info("Nenhuma ordem aberta para cancelar.", extra={"event": "cancel_skip"})
            return None

        cancel_resp = self._with_retry(lambda: self.client.cancel_orders(order_ids))
        for order_id in order_ids:
            self.known_orders.pop(order_id, None)

        logger.info(
            "Ordens canceladas",
            extra={"event": "cancel", "count": len(order_ids), "order_ids": order_ids},
        )
        self._save_state()
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
                    "Tentando novamente apos falha de API",
                    extra={
                        "event": "retry",
                        "attempt": attempt,
                        "max_retries": self.settings.max_retries,
                    },
                )
                time.sleep(self.settings.retry_delay_seconds)

    def _build_quote_context(self) -> QuoteContext:
        market = self._select_market()
        self.current_token_id = market["token_id"]
        previous_price = self.last_market_prices.get(market["token_id"])
        price_change_ratio = self._calculate_price_change_ratio(previous_price, market["midpoint"])
        spread = self._determine_spread(market)
        quotes = self._build_quotes(market["midpoint"], spread)

        logger.info(
            "Quotes calculadas",
            extra={
                "event": "quotes",
                "token_id": market["token_id"],
                "price": market["midpoint"],
                "previous_price": previous_price,
                "price_change_ratio": round(price_change_ratio, 6),
                "buy_price": quotes["buy"].price,
                "sell_price": quotes["sell"].price,
                "size": self.settings.size,
                "spread": spread,
                "book_spread": market["book_spread"],
                "liquidity_score": market["liquidity_score"],
            },
        )
        return QuoteContext(
            token_id=market["token_id"],
            current_price=market["midpoint"],
            previous_price=previous_price,
            price_change_ratio=price_change_ratio,
            spread=spread,
            book_spread=market["book_spread"],
            liquidity_score=market["liquidity_score"],
            quotes=quotes,
        )

    def _place_quotes(
        self,
        context: QuoteContext,
        execution_plan: ExecutionPlan,
        token_balance: float | None,
        open_orders: list[dict[str, Any]],
    ) -> None:
        self._place_quote_if_allowed(context.token_id, execution_plan.buy, token_balance, open_orders)
        self._place_quote_if_allowed(context.token_id, execution_plan.sell, token_balance, open_orders)

    def _place_quote_if_allowed(
        self,
        token_id: str,
        quote: Quote,
        token_balance: float | None,
        open_orders: list[dict[str, Any]],
    ) -> None:
        if self._has_duplicate_open_order(open_orders, quote):
            logger.info(
                "Ordem duplicada detectada no preco alvo; criacao ignorada.",
                extra={
                    "event": "dedupe",
                    "token_id": token_id,
                    "side": quote.side,
                    "price": quote.price,
                    "size": quote.size,
                },
            )
            return

        if not self._position_allows_side(quote.side, token_balance):
            logger.info(
                "Ordem bloqueada por controle de posicao.",
                extra={"event": "skip_position", "token_id": token_id, "side": quote.side, "price": quote.price},
            )
            return

        if not self._risk_allows_order(token_id, quote, token_balance):
            return

        if quote.side == "buy" and not self._has_buy_balance(quote):
            logger.warning(
                "Saldo insuficiente para BUY, ordem pulada.",
                extra={"event": "skip_buy_no_balance", "token_id": token_id, "price": quote.price, "size": quote.size},
            )
            return

        if quote.side == "sell" and not self._has_sell_balance(quote):
            logger.warning(
                "Saldo/posicao insuficiente para SELL, ordem pulada.",
                extra={"event": "skip_sell_no_balance", "token_id": token_id, "price": quote.price, "size": quote.size},
            )
            return

        response = self._with_retry(
            lambda: self.client.place_limit_order(
                token_id=token_id,
                side=quote.side,
                price=quote.price,
                size=quote.size,
            )
        )
        self._track_order_id(response)
        logger.info(
            "Ordem criada",
            extra={
                "event": "place_order",
                "token_id": token_id,
                "side": quote.side,
                "price": quote.price,
                "size": quote.size,
                "order_id": self.client.extract_order_id(response),
            },
        )

    def _build_execution_plan(self, context: QuoteContext) -> ExecutionPlan:
        net_position = self._get_net_position(context.token_id)
        buy_size = context.quotes["buy"].size
        sell_size = context.quotes["sell"].size

        if net_position > 0:
            sell_size = max(sell_size, abs(net_position))
        elif net_position < 0:
            buy_size = max(buy_size, abs(net_position))

        plan = ExecutionPlan(
            buy=Quote(side="buy", price=context.quotes["buy"].price, size=round(buy_size, 4)),
            sell=Quote(side="sell", price=context.quotes["sell"].price, size=round(sell_size, 4)),
            net_position=net_position,
        )
        logger.info(
            "Plano de execucao calculado",
            extra={
                "event": "execution_plan",
                "token_id": context.token_id,
                "buy_price": plan.buy.price,
                "buy_size": plan.buy.size,
                "sell_price": plan.sell.price,
                "sell_size": plan.sell.size,
                "net_position": net_position,
            },
        )
        return plan

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
        previous_price = self.last_market_prices.get(market["token_id"])
        price_change_ratio = self._calculate_price_change_ratio(previous_price, market["midpoint"])
        normalized = min(1.0, max(0.0, (liquidity - low) / (high - low)))
        adaptive_spread = max_spread - ((max_spread - min_spread) * normalized)
        volatility_multiplier = self._volatility_spread_multiplier(price_change_ratio)
        adaptive_spread = adaptive_spread * volatility_multiplier
        adaptive_spread = min(max_spread, max(min_spread, adaptive_spread))
        final_spread = max(market["book_spread"], adaptive_spread)
        logger.info(
            "Spread dinamico calculado",
            extra={
                "event": "dynamic_spread",
                "token_id": market["token_id"],
                "previous_price": previous_price,
                "price_change_ratio": round(price_change_ratio, 6),
                "liquidity_score": liquidity,
                "book_spread": market["book_spread"],
                "volatility_multiplier": volatility_multiplier,
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

    def _should_refresh_orders(self, context: QuoteContext, open_orders: list[dict[str, Any]]) -> bool:
        if not open_orders:
            logger.info("Sem ordens abertas; refresh desnecessario.", extra={"event": "refresh_skip_empty", "token_id": context.token_id})
            return False

        if context.price_change_ratio >= self.settings.price_refresh_threshold:
            logger.info(
                "Ordens antigas marcadas para refresh por movimento de preco.",
                extra={
                    "event": "refresh_price_move",
                    "token_id": context.token_id,
                    "price_change_ratio": round(context.price_change_ratio, 6),
                    "refresh_threshold": self.settings.price_refresh_threshold,
                },
            )
            return True

        for side, quote in context.quotes.items():
            if not self._has_matching_open_order(open_orders, quote):
                logger.info(
                    "Ordens antigas marcadas para refresh por desalinhamento com novo preco.",
                    extra={"event": "refresh_requote", "token_id": context.token_id, "side": side, "price": quote.price},
                )
                return True

        logger.info(
            "Ordens atuais ainda validas; mantendo no book.",
            extra={"event": "refresh_keep", "token_id": context.token_id, "price_change_ratio": round(context.price_change_ratio, 6)},
        )
        return False

    @staticmethod
    def _calculate_price_change_ratio(previous_price: float | None, current_price: float) -> float:
        if previous_price in {None, 0}:
            return 0.0
        return abs(current_price - previous_price) / abs(previous_price)

    def _volatility_spread_multiplier(self, price_change_ratio: float) -> float:
        if price_change_ratio >= self.settings.high_volatility_threshold:
            return self.settings.high_volatility_spread_multiplier
        if price_change_ratio <= self.settings.low_volatility_threshold:
            return self.settings.low_volatility_spread_multiplier
        return 1.0

    def _has_duplicate_open_order(self, open_orders: list[dict[str, Any]], quote: Quote) -> bool:
        for order in open_orders:
            if self._extract_side(order) != quote.side:
                continue
            if self._matches_quote(order, quote):
                return True
        return False

    def _has_matching_open_order(self, open_orders: list[dict[str, Any]], quote: Quote) -> bool:
        return self._has_duplicate_open_order(open_orders, quote)

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
        token_ids.extend(self.net_positions.keys())
        return list(dict.fromkeys(token_ids))

    def _get_net_position(self, token_id: str) -> float:
        return float(self.net_positions.get(token_id, 0.0))

    def _register_fill(self, token_id: str, side: str, size: float, is_partial: bool = False) -> None:
        delta = size if side == "buy" else -size
        updated = round(self._get_net_position(token_id) + delta, 4)
        self.net_positions[token_id] = updated
        logger.info(
            "Posicao local atualizada por fill",
            extra={
                "event": "position_update",
                "token_id": token_id,
                "side": side,
                "size": size,
                "is_partial": is_partial,
                "net_position": updated,
            },
        )

    def _save_state(self) -> None:
        self.state_store.save(
            BotState(
                known_orders=self.known_orders,
                net_positions=self.net_positions,
                last_market_prices=self.last_market_prices,
                trades_executed=self.trades_executed,
                total_pnl_realized=self.total_pnl_realized,
                last_metrics_log_time=self.last_metrics_log_time,
                consecutive_api_errors=self.consecutive_api_errors,
                last_api_error_time=self.last_api_error_time,
            )
        )

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
                token_id = str(snapshot.get("token_id") or self.current_token_id)
                side = snapshot.get("side") or self._extract_side(details)
                filled_size = self._extract_size(details) or snapshot.get("size") or 0.0
                original_size = snapshot.get("size") or filled_size
                
                # EXECUTION CONTROL: Detect partial fills
                is_partial = float(filled_size) < float(original_size)
                
                self._register_fill(
                    token_id=token_id,
                    side=side,
                    size=float(filled_size),
                    is_partial=is_partial,
                )
                
                # EXECUTION CONTROL: Ensure hedge position
                opposite_side = "sell" if side == "buy" else "buy"
                remaining_size = float(filled_size)
                
                fill_info = {
                    "order_id": order_id,
                    "token_id": token_id,
                    "side": side,
                    "price": self._extract_price(details) or snapshot.get("price"),
                    "filled_size": filled_size,
                    "original_size": original_size,
                    "is_partial": is_partial,
                    "status": status,
                    "details": details,
                    "net_position": self._get_net_position(token_id),
                }
                filled_orders.append(fill_info)
                
                self.trades_executed += 1
                logger.info(
                    "Ordem executada",
                    extra={
                        "event": "filled",
                        "partial": is_partial,
                        "remaining_size": remaining_size if is_partial else 0,
                        **fill_info,
                    },
                )
            elif status in CLOSED_STATUSES:
                logger.info(
                    "Ordem encerrada",
                    extra={"event": "closed", "order_id": order_id, "status": status},
                )
            else:
                still_open[order_id] = {**snapshot, **details}

        self.known_orders = still_open
        self._save_state()
        return filled_orders

    def _has_buy_balance(self, quote: Quote) -> bool:
        collateral = self._with_retry(self.client.get_collateral_balance)
        if collateral is None:
            logger.warning("Nao foi possivel validar collateral; prosseguindo com BUY.", extra={"event": "buy_balance_unknown"})
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
            logger.warning("Nao foi possivel validar posicao do token; prosseguindo com SELL.", extra={"event": "sell_balance_unknown"})
            return True

        logger.info(
            "Balance check SELL",
            extra={"event": "sell_balance", "token_balance": token_balance, "required": quote.size},
        )
        return token_balance >= quote.size

    def _get_token_balance(self) -> float | None:
        return self._with_retry(lambda: self.client.get_token_balance(self.current_token_id))

    def _position_allows_side(self, side: str, token_balance: float | None) -> bool:
        local_net_position = self._get_net_position(self.current_token_id)
        if side == "buy" and local_net_position > 0:
            logger.info(
                "BUY bloqueado para evitar acumulacao unilateral.",
                extra={"event": "skip_unilateral_buy", "token_id": self.current_token_id, "net_position": local_net_position},
            )
            return False
        if side == "sell" and local_net_position < 0:
            logger.info(
                "SELL bloqueado para evitar acumulacao unilateral.",
                extra={"event": "skip_unilateral_sell", "token_id": self.current_token_id, "net_position": local_net_position},
            )
            return False

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
                "local_net_position": local_net_position,
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

    def _risk_allows_order(self, token_id: str, quote: Quote, token_balance: float | None) -> bool:
        if not self._within_max_exposure(token_id, quote):
            return False
        if quote.side == "buy":
            return self._has_buy_capacity(quote)
        if quote.side == "sell":
            return self._has_sell_capacity(quote, token_balance)
        return True

    def _within_max_exposure(self, token_id: str, quote: Quote) -> bool:
        current_exposure = self._get_net_position(token_id)
        projected_exposure = current_exposure + quote.size if quote.side == "buy" else current_exposure - quote.size
        allowed = abs(projected_exposure) <= self.settings.max_position_size
        logger.info(
            "Risk check exposure",
            extra={
                "event": "risk_exposure",
                "token_id": token_id,
                "side": quote.side,
                "size": quote.size,
                "net_position": current_exposure,
                "projected_exposure": round(projected_exposure, 4),
                "max_position_size": self.settings.max_position_size,
            },
        )
        if not allowed:
            logger.warning(
                "Ordem bloqueada por limite maximo de exposicao.",
                extra={
                    "event": "skip_max_exposure",
                    "token_id": token_id,
                    "side": quote.side,
                    "size": quote.size,
                    "net_position": current_exposure,
                    "projected_exposure": round(projected_exposure, 4),
                    "max_position_size": self.settings.max_position_size,
                },
            )
        return allowed

    def _has_buy_capacity(self, quote: Quote) -> bool:
        collateral = self._with_retry(self.client.get_collateral_balance)
        if collateral is None:
            logger.warning(
                "Nao foi possivel validar collateral para risco; BUY bloqueado.",
                extra={"event": "risk_buy_balance_unknown"},
            )
            return False

        max_usable_collateral = collateral * self.settings.max_balance_usage_pct
        required = (quote.price * quote.size) + self.settings.min_collateral_buffer
        allowed = max_usable_collateral >= required
        logger.info(
            "Risk check BUY balance",
            extra={
                "event": "risk_buy_balance",
                "collateral": collateral,
                "max_usable_collateral": round(max_usable_collateral, 4),
                "required": round(required, 4),
            },
        )
        if not allowed:
            logger.warning(
                "BUY bloqueado para preservar reserva de saldo.",
                extra={
                    "event": "skip_buy_risk_balance",
                    "collateral": collateral,
                    "max_usable_collateral": round(max_usable_collateral, 4),
                    "required": round(required, 4),
                },
            )
        return allowed

    def _has_sell_capacity(self, quote: Quote, token_balance: float | None) -> bool:
        if token_balance is None:
            logger.warning(
                "Nao foi possivel validar saldo do token para risco; SELL bloqueado.",
                extra={"event": "risk_sell_balance_unknown"},
            )
            return False

        max_usable_tokens = token_balance * self.settings.max_balance_usage_pct
        allowed = max_usable_tokens >= quote.size
        logger.info(
            "Risk check SELL balance",
            extra={
                "event": "risk_sell_balance",
                "token_balance": token_balance,
                "max_usable_tokens": round(max_usable_tokens, 4),
                "required": quote.size,
            },
        )
        if not allowed:
            logger.warning(
                "SELL bloqueado para preservar reserva de saldo.",
                extra={
                    "event": "skip_sell_risk_balance",
                    "token_balance": token_balance,
                    "max_usable_tokens": round(max_usable_tokens, 4),
                    "required": quote.size,
                },
            )
        return allowed

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
            self._save_state()

    def _remember_orders(self, orders: list[dict[str, Any]]) -> None:
        for order in orders:
            order_id = self.client.extract_order_id(order)
            if order_id:
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

    # ========== KILL SWITCH IMPLEMENTATION ==========
    def _check_kill_switch(self) -> bool:
        """Check if kill switch should be activated."""
        # Check manual kill switch flag file
        flag_file_path = Path(self.settings.kill_switch_flag_file)
        if flag_file_path.exists():
            logger.critical(
                "KILL SWITCH: Flag file detected",
                extra={"event": "kill_switch_flag_file", "path": str(flag_file_path)},
            )
            return True
        return False

    def _check_balance_safety(self) -> bool:
        """Check if balance is above minimum threshold."""
        collateral = self._with_retry(self.client.get_collateral_balance)
        if collateral is None:
            logger.warning("Unable to check balance safety", extra={"event": "balance_check_failed"})
            return True
        
        is_safe = collateral >= self.settings.min_balance_threshold
        if not is_safe:
            logger.critical(
                "Balance below minimum threshold",
                extra={
                    "event": "balance_safety_failed",
                    "balance": collateral,
                    "minimum": self.settings.min_balance_threshold,
                },
            )
        return is_safe

    # ========== API SYNCHRONIZATION (CRÍTICO) ==========
    def _sync_orders_with_api(self) -> None:
        """
        CRITICAL: Synchronize known_orders with API.
        Remove local orders that no longer exist on API.
        This ensures API is the source of truth.
        """
        try:
            api_open_orders = self._with_retry(
                lambda: self.client.get_open_orders(self.current_token_id)
            )
            api_order_ids = {
                self.client.extract_order_id(order)
                for order in api_open_orders
                if self.client.extract_order_id(order)
            }
            
            # Remove orders from local state that don't exist on API
            stale_order_ids = set(self.known_orders.keys()) - api_order_ids
            if stale_order_ids:
                logger.info(
                    "Removendo ordens stale do estado local (nao encontradas na API)",
                    extra={
                        "event": "api_sync_stale_removal",
                        "count": len(stale_order_ids),
                        "order_ids": list(stale_order_ids),
                    },
                )
                for order_id in stale_order_ids:
                    del self.known_orders[order_id]
            
            # Update local known_orders with API data
            for order in api_open_orders:
                order_id = self.client.extract_order_id(order)
                if order_id:
                    self.known_orders[order_id] = self._build_order_snapshot(order)
            
            self.last_api_sync_time = time.time()
            logger.info(
                "API sync completo",
                extra={
                    "event": "api_sync_complete",
                    "api_orders": len(api_order_ids),
                    "local_orders": len(self.known_orders),
                    "stale_removed": len(stale_order_ids),
                },
            )
        except PolymarketApiError as exc:
            logger.exception(
                "Falha na sincronizacao com API",
                extra={"event": "api_sync_failed", "error": str(exc)},
            )
            raise

    # ========== SMART CANCELLATION LOGIC ==========
    def _smart_cancel_open_orders(
        self,
        open_orders: list[dict[str, Any]],
        context: QuoteContext,
    ) -> None:
        """
        SMART CANCELLATION: Cancel orders intelligently:
        - Cancel orders that are out of current price range
        - Or cancel ALL orders if price moved significantly
        - Avoid unnecessary churn
        """
        if not open_orders:
            return
        
        orders_out_of_range = []
        for order in open_orders:
            order_side = self._extract_side(order)
            order_price = self._extract_price(order)
            
            if order_price is None:
                continue
            
            # Check if order is outside current quote range
            buy_price = context.quotes["buy"].price
            sell_price = context.quotes["sell"].price
            
            if order_side == "buy" and order_price < buy_price - self.settings.price_tolerance:
                orders_out_of_range.append(order)
                logger.info(
                    "BUY order fora do range (preco muito baixo)",
                    extra={
                        "event": "order_out_of_range_buy",
                        "order_price": order_price,
                        "new_buy_price": buy_price,
                    },
                )
            elif order_side == "sell" and order_price > sell_price + self.settings.price_tolerance:
                orders_out_of_range.append(order)
                logger.info(
                    "SELL order fora do range (preco muito alto)",
                    extra={
                        "event": "order_out_of_range_sell",
                        "order_price": order_price,
                        "new_sell_price": sell_price,
                    },
                )
        
        if orders_out_of_range:
            self.cancel_open_orders(orders_out_of_range)

    # ========== REPOSITIONING CONTROL ==========
    def _should_refresh_orders_smart(
        self,
        context: QuoteContext,
        open_orders: list[dict[str, Any]],
    ) -> bool:
        """
        REPOSITIONING CONTROL: Reduce order churn
        - Only recreate orders if price changes above threshold
        - Avoid unnecessary cancel/recreate cycles
        """
        if not open_orders:
            logger.info(
                "Sem ordens abertas; refresh desnecessario.",
                extra={"event": "refresh_skip_empty", "token_id": context.token_id},
            )
            return False

        # Check if price moved significantly (above reposition threshold)
        if context.price_change_ratio >= self.settings.reposition_price_threshold:
            logger.info(
                "Ordens marcadas para refresh por movimento significativo de preco",
                extra={
                    "event": "refresh_price_threshold",
                    "token_id": context.token_id,
                    "price_change_ratio": round(context.price_change_ratio, 6),
                    "threshold": self.settings.reposition_price_threshold,
                },
            )
            return True

        # Check if any quote mismatches current prices
        for side, quote in context.quotes.items():
            if not self._has_matching_open_order(open_orders, quote):
                logger.info(
                    "Ordens marcadas para refresh por desalinhamento com novo preco",
                    extra={
                        "event": "refresh_price_mismatch",
                        "token_id": context.token_id,
                        "side": side,
                        "quote_price": quote.price,
                    },
                )
                return True

        logger.info(
            "Ordens atuais ainda validas; mantendo no book",
            extra={
                "event": "refresh_keep",
                "token_id": context.token_id,
                "price_change_ratio": round(context.price_change_ratio, 6),
            },
        )
        return False

    # ========== PERFORMANCE METRICS LOGGING ==========
    def _log_performance_metrics(self) -> None:
        """
        PERFORMANCE LOGGING: Log key metrics periodically
        - Estimated PnL
        - Trades executed
        - Order execution rate
        - Time-weighted metrics
        """
        current_time = time.time()
        time_since_last_log = current_time - self.last_metrics_log_time
        
        if time_since_last_log < self.settings.metrics_log_interval_seconds:
            return
        
        self.last_metrics_log_time = current_time
        
        # Calculate estimated PnL (based on net positions and last known prices)
        estimated_pnl = 0.0
        for token_id, net_position in self.net_positions.items():
            if net_position != 0:
                last_price = self.last_market_prices.get(token_id, 0.0)
                if last_price > 0:
                    # Simple unrealized PnL estimate
                    estimated_pnl += net_position * last_price
        
        # Calculate execution rate
        execution_rate = (
            self.trades_executed / (time_since_last_log / 3600)
            if time_since_last_log > 0
            else 0
        )
        
        # Log comprehensive metrics
        logger.info(
            "Performance Metrics",
            extra={
                "event": "performance_metrics",
                "trades_executed": self.trades_executed,
                "total_pnl_realized": round(self.total_pnl_realized, 4),
                "estimated_unrealized_pnl": round(estimated_pnl, 4),
                "execution_rate_per_hour": round(execution_rate, 2),
                "open_orders_count": len(self.known_orders),
                "tracked_positions_count": len(self.net_positions),
                "consecutive_api_errors": self.consecutive_api_errors,
                "consecutive_execution_errors": self.consecutive_execution_errors,
                "time_since_last_api_error": round(current_time - self.last_api_error_time, 1)
                if self.last_api_error_time > 0
                else 0,
            },
        )
