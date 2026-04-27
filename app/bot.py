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
        # HARDENING: Synchronize with API - ensure API is source of truth
        try:
            self._sync_orders_with_api()
        except PolymarketApiError as exc:
            logger.critical(
                "FAIL-SAFE: API sync failed at cycle start - stopping iteration",
                extra={
                    "event": "cycle_start_api_sync_failed",
                    "error": str(exc),
                },
            )
            raise
        
        # HARDENING: Validate state consistency early
        if not self._validate_state_consistency():
            logger.critical(
                "FAIL-SAFE: State inconsistency detected at cycle start",
                extra={"event": "cycle_start_state_invalid"},
            )
            self._save_state()
            raise PolymarketApiError("State consistency check failed")
        
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
        
        # HARDENING: Verify position hedges after fills
        if not self._validate_position_hedges():
            logger.warning(
                "Hedge validation falhou durante ciclo",
                extra={"event": "cycle_hedge_validation_failed"},
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
        
        # HARDENING: Final state consistency check
        if not self._validate_state_consistency():
            logger.warning(
                "HARDENING: State inconsistency detected at cycle end - but cycle complete",
                extra={"event": "cycle_end_state_validation_warning"},
            )
        
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
        cycle_count = 0
        while True:
            cycle_count += 1
            
            # Check kill switch
            if self._check_kill_switch():
                logger.critical("KILL SWITCH ACTIVATED. Shutting down bot.", extra={"event": "kill_switch"})
                break
            
            logger.info(
                "Iniciando ciclo de execucao",
                extra={"event": "cycle_start", "cycle_number": cycle_count},
            )
                
            try:
                self.run_once()
                # Reset error counters on success
                self.consecutive_api_errors = 0
                self.consecutive_execution_errors = 0
                self.last_api_error_time = 0.0
                logger.info(
                    "Ciclo completo com sucesso",
                    extra={
                        "event": "cycle_success",
                        "cycle_number": cycle_count,
                        "open_orders": len(self.known_orders),
                    },
                )
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
                        "cycle_number": cycle_count,
                    },
                )
                
                # Validate error streak
                if self.consecutive_api_errors >= self.settings.max_api_failure_streak:
                    logger.critical(
                        "KILL SWITCH: Max API failures exceeded",
                        extra={
                            "event": "kill_switch_api_failures",
                            "consecutive_errors": self.consecutive_api_errors,
                            "limit": self.settings.max_api_failure_streak,
                        },
                    )
                    self._save_state()
                    break
            except Exception as exc:  # noqa: BLE001
                self.consecutive_execution_errors += 1
                logger.exception(
                    "Erro inesperado no loop principal",
                    extra={
                        "event": "unexpected_error",
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                        "consecutive_errors": self.consecutive_execution_errors,
                        "limit": self.settings.max_consecutive_errors,
                        "cycle_number": cycle_count,
                    },
                )
                
                # Validate error streak
                if self.consecutive_execution_errors >= self.settings.max_consecutive_errors:
                    logger.critical(
                        "KILL SWITCH: Max execution errors exceeded",
                        extra={
                            "event": "kill_switch_exec_errors",
                            "consecutive_errors": self.consecutive_execution_errors,
                            "limit": self.settings.max_consecutive_errors,
                        },
                    )
                    self._save_state()
                    break
            finally:
                self._save_state()
                logger.info(
                    "Ciclo encerrado; aguardando proximo intervalo",
                    extra={
                        "event": "cycle_end",
                        "sleep_seconds": self.settings.loop_interval_seconds,
                    },
                )
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

        logger.info(
            "CANCELAMENTO: Iniciando cancelamento de ordens",
            extra={
                "event": "cancel_start",
                "count": len(order_ids),
                "order_ids": order_ids,
            },
        )
        
        try:
            cancel_resp = self._with_retry(lambda: self.client.cancel_orders(order_ids))
            
            # Remove from local tracking
            for order_id in order_ids:
                removed = self.known_orders.pop(order_id, None)
                if removed:
                    logger.info(
                        "Ordem removida do estado local apos cancelamento",
                        extra={
                            "event": "order_removed_from_state",
                            "order_id": order_id,
                            "token_id": removed.get("token_id"),
                            "side": removed.get("side"),
                        },
                    )

            logger.info(
                "CANCELAMENTO: Sucesso",
                extra={
                    "event": "cancel_success",
                    "count": len(order_ids),
                    "order_ids": order_ids,
                },
            )
            self._save_state()
            return cancel_resp
        except PolymarketApiError as exc:
            logger.exception(
                "FAIL-SAFE: Falha ao cancelar ordens",
                extra={
                    "event": "cancel_failed",
                    "count": len(order_ids),
                    "error": str(exc),
                },
            )
            # DON'T remove from local state if API call failed
            # This keeps consistency
            raise

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
        """Place quotes with comprehensive pre-order validation."""
        # HARDENING: Pre-order validation BEFORE placing any orders
        if not self._pre_order_validation(context, execution_plan):
            logger.warning(
                "PRE-ORDER: Validacao falhou - pulando colocacao de ordens neste ciclo",
                extra={"event": "place_quotes_validation_failed"},
            )
            return
        
        self._place_quote_if_allowed(context.token_id, execution_plan.buy, token_balance, open_orders)
        self._place_quote_if_allowed(context.token_id, execution_plan.sell, token_balance, open_orders)

    def _place_quote_if_allowed(
        self,
        token_id: str,
        quote: Quote,
        token_balance: float | None,
        open_orders: list[dict[str, Any]],
    ) -> None:
        decision_context = {
            "token_id": token_id,
            "side": quote.side,
            "price": quote.price,
            "size": quote.size,
        }
        
        # Check 1: Duplicate detection
        if self._has_duplicate_open_order(open_orders, quote):
            self._log_decision(
                "duplicate_order_check",
                False,
                {
                    **decision_context,
                    "reason": "Ordem duplicada detectada no preco alvo",
                },
            )
            return

        # Check 2: Position allows side
        if not self._position_allows_side(quote.side, token_balance):
            self._log_decision(
                "position_check",
                False,
                {
                    **decision_context,
                    "reason": "Posicao nao permite este lado",
                    "token_balance": token_balance,
                },
            )
            return

        # Check 3: Risk allows order
        if not self._risk_allows_order(token_id, quote, token_balance):
            self._log_decision(
                "risk_check",
                False,
                {**decision_context, "reason": "Falha em verificacao de risco"},
            )
            return

        # Check 4: Buy balance
        if quote.side == "buy" and not self._has_buy_balance(quote):
            self._log_decision(
                "buy_balance_check",
                False,
                {
                    **decision_context,
                    "reason": "Saldo insuficiente para BUY",
                    "required": (quote.price * quote.size) + self.settings.min_collateral_buffer,
                    "available": self._with_retry(self.client.get_collateral_balance),
                },
            )
            return

        # Check 5: Sell balance
        if quote.side == "sell" and not self._has_sell_balance(quote):
            self._log_decision(
                "sell_balance_check",
                False,
                {
                    **decision_context,
                    "reason": "Saldo/posicao insuficiente para SELL",
                    "required": quote.size,
                    "available": token_balance,
                },
            )
            return

        # All checks passed - place order
        try:
            response = self._with_retry(
                lambda: self.client.place_limit_order(
                    token_id=token_id,
                    side=quote.side,
                    price=quote.price,
                    size=quote.size,
                )
            )
            self._track_order_id(response)
            
            self._log_decision(
                "place_order",
                True,
                {
                    **decision_context,
                    "order_id": self.client.extract_order_id(response),
                    "reason": "Todas as verificacoes passaram",
                },
            )
            
            logger.info(
                "Ordem criada com sucesso",
                extra={
                    "event": "place_order",
                    "token_id": token_id,
                    "side": quote.side,
                    "price": quote.price,
                    "size": quote.size,
                    "order_id": self.client.extract_order_id(response),
                },
            )
        except PolymarketApiError as exc:
            self._log_decision(
                "place_order",
                False,
                {
                    **decision_context,
                    "reason": f"Erro ao criar ordem: {str(exc)}",
                },
            )
            logger.exception(
                "Falha ao criar ordem",
                extra={
                    "event": "place_order_failed",
                    "token_id": token_id,
                    "side": quote.side,
                    "error": str(exc),
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

    # ========== FAIL-SAFE SYSTEM: State Consistency ==========
    def _validate_state_consistency(self) -> bool:
        """
        FAIL-SAFE: Detect state inconsistencies.
        Compares local state against API reality.
        Returns False if critical inconsistency detected.
        """
        try:
            api_orders = self._with_retry(
                lambda: self.client.get_open_orders(self.current_token_id)
            )
            api_order_ids = {
                self.client.extract_order_id(order)
                for order in api_orders
                if self.client.extract_order_id(order)
            }
            
            local_order_ids = set(self.known_orders.keys())
            
            # Check for ghost orders (local but not on API)
            ghost_orders = local_order_ids - api_order_ids
            if ghost_orders:
                logger.warning(
                    "INCONSISTENCIA: Ordens ghost encontradas (local mas nao na API)",
                    extra={
                        "event": "state_inconsistency_ghost_orders",
                        "ghost_count": len(ghost_orders),
                        "ghost_ids": list(ghost_orders)[:5],  # Log first 5
                    },
                )
            
            # Check for unknown orders (API but not local)
            unknown_orders = api_order_ids - local_order_ids
            if unknown_orders:
                logger.warning(
                    "INCONSISTENCIA: Ordens desconhecidas encontradas (API mas nao local)",
                    extra={
                        "event": "state_inconsistency_unknown_orders",
                        "unknown_count": len(unknown_orders),
                    },
                )
                # Import these orders into known_orders
                for order in api_orders:
                    order_id = self.client.extract_order_id(order)
                    if order_id in unknown_orders:
                        self.known_orders[order_id] = self._build_order_snapshot(order)
                        logger.info(
                            "Ordem desconhecida importada para estado local",
                            extra={
                                "event": "unknown_order_imported",
                                "order_id": order_id,
                            },
                        )
            
            # Detailed price validation
            for order_id, api_order in zip(
                [self.client.extract_order_id(o) for o in api_orders],
                api_orders,
            ):
                if order_id not in self.known_orders:
                    continue
                    
                local_data = self.known_orders[order_id]
                api_price = self._extract_price(api_order)
                local_price = local_data.get("price")
                
                if api_price and local_price:
                    price_diff = abs(api_price - local_price)
                    if price_diff > 0.01:  # Price mismatch > $0.01
                        logger.warning(
                            "Inconsistencia de preco detectada",
                            extra={
                                "event": "price_mismatch",
                                "order_id": order_id,
                                "api_price": api_price,
                                "local_price": local_price,
                                "diff": price_diff,
                            },
                        )
            
            # If ghost orders exceed threshold, it's critical
            if len(ghost_orders) > 5:
                logger.critical(
                    "FAIL-SAFE: Inconsistencia critica de estado detectada",
                    extra={
                        "event": "critical_state_inconsistency",
                        "ghost_count": len(ghost_orders),
                    },
                )
                return False
                
            return True
            
        except PolymarketApiError as exc:
            logger.exception(
                "Falha ao validar consistencia de estado",
                extra={"event": "state_validation_failed", "error": str(exc)},
            )
            return False

    # ========== POSITION SAFETY: Hedge Detection & Auto-Hedge ==========
    def _validate_position_hedges(self) -> bool:
        """
        POSITION SAFETY: Ensure all positions have opposite hedges.
        If unhedged position detected for too long, create hedge automatically.
        """
        unhedged_positions: dict[str, tuple[str, float]] = {}
        
        for token_id, net_position in self.net_positions.items():
            if net_position == 0:
                continue
            
            # Position is long (positive) - need SELL hedge
            if net_position > 0:
                has_sell_order = any(
                    self._extract_side(order) == "sell"
                    for order in [
                        self.known_orders[oid]["raw"]
                        for oid in self.known_orders
                        if self.known_orders[oid].get("token_id") == token_id
                    ]
                    if "raw" in self.known_orders.get(oid, {})
                )
                if not has_sell_order:
                    unhedged_positions[token_id] = ("sell", net_position)
                    logger.warning(
                        "POSITION SAFETY: Posicao LONG sem hedge SELL",
                        extra={
                            "event": "unhedged_position_long",
                            "token_id": token_id,
                            "position_size": net_position,
                        },
                    )
            
            # Position is short (negative) - need BUY hedge
            elif net_position < 0:
                has_buy_order = any(
                    self._extract_side(order) == "buy"
                    for order in [
                        self.known_orders[oid]["raw"]
                        for oid in self.known_orders
                        if self.known_orders[oid].get("token_id") == token_id
                    ]
                    if "raw" in self.known_orders.get(oid, {})
                )
                if not has_buy_order:
                    unhedged_positions[token_id] = ("buy", abs(net_position))
                    logger.warning(
                        "POSITION SAFETY: Posicao SHORT sem hedge BUY",
                        extra={
                            "event": "unhedged_position_short",
                            "token_id": token_id,
                            "position_size": net_position,
                        },
                    )
        
        if not unhedged_positions:
            return True
        
        # Try to auto-hedge unhedged positions
        logger.info(
            "POSITION SAFETY: Criando hedges automaticamente",
            extra={
                "event": "auto_hedge_start",
                "unhedged_count": len(unhedged_positions),
            },
        )
        
        for token_id, (hedge_side, hedge_size) in unhedged_positions.items():
            try:
                # Get current price for hedge placement
                market = self._with_retry(
                    lambda token_id=token_id: self.client.get_market_snapshot(token_id)
                )
                base_price = market["midpoint"]
                
                # Place hedge order slightly worse (more conservative)
                if hedge_side == "buy":
                    hedge_price = max(0.001, base_price - 0.01)  # 1 cent worse for buy
                else:
                    hedge_price = min(0.999, base_price + 0.01)  # 1 cent worse for sell
                
                response = self._with_retry(
                    lambda: self.client.place_limit_order(
                        token_id=token_id,
                        side=hedge_side,
                        price=round(hedge_price, 4),
                        size=round(hedge_size, 4),
                    )
                )
                
                self._track_order_id(response)
                logger.info(
                    "POSITION SAFETY: Hedge criado automaticamente",
                    extra={
                        "event": "auto_hedge_success",
                        "token_id": token_id,
                        "hedge_side": hedge_side,
                        "hedge_size": hedge_size,
                        "hedge_price": hedge_price,
                        "order_id": self.client.extract_order_id(response),
                    },
                )
            except PolymarketApiError as exc:
                logger.exception(
                    "FAIL-SAFE: Falha ao criar hedge automatico",
                    extra={
                        "event": "auto_hedge_failed",
                        "token_id": token_id,
                        "error": str(exc),
                    },
                )
                return False
        
        return True

    # ========== PRE-ORDER SYNCHRONIZATION & VALIDATION ==========
    def _pre_order_validation(self, context: QuoteContext, execution_plan: ExecutionPlan) -> bool:
        """
        Validate before placing ANY order:
        1. Synchronize with API
        2. Check state consistency
        3. Verify position hedges
        4. Confirm balance
        5. Log all decisions
        """
        logger.info(
            "PRE-ORDER: Iniciando validacao completa",
            extra={
                "event": "pre_order_validation_start",
                "token_id": context.token_id,
                "plan_buy_price": execution_plan.buy.price,
                "plan_sell_price": execution_plan.sell.price,
            },
        )
        
        # 1. Sync with API
        try:
            self._sync_orders_with_api()
            logger.info(
                "PRE-ORDER: Sincronizacao com API completa",
                extra={
                    "event": "pre_order_api_sync_ok",
                    "open_orders": len(self.known_orders),
                },
            )
        except PolymarketApiError as exc:
            logger.critical(
                "PRE-ORDER: Falha na sincronizacao com API - bloqueando ordens",
                extra={
                    "event": "pre_order_api_sync_failed",
                    "error": str(exc),
                },
            )
            return False
        
        # 2. Check state consistency
        if not self._validate_state_consistency():
            logger.critical(
                "PRE-ORDER: Inconsistencia de estado detectada - bloqueando ordens",
                extra={"event": "pre_order_state_invalid"},
            )
            return False
        
        logger.info(
            "PRE-ORDER: Consistencia de estado validada",
            extra={"event": "pre_order_state_ok"},
        )
        
        # 3. Verify position hedges
        if not self._validate_position_hedges():
            logger.critical(
                "PRE-ORDER: Falha ao validar hedges de posicao - bloqueando ordens",
                extra={"event": "pre_order_hedge_validation_failed"},
            )
            return False
        
        logger.info(
            "PRE-ORDER: Hedges de posicao validados",
            extra={"event": "pre_order_hedges_ok"},
        )
        
        # 4. Confirm balance
        collateral = self._with_retry(self.client.get_collateral_balance)
        token_balance = self._get_token_balance()
        
        if collateral is None or token_balance is None:
            logger.warning(
                "PRE-ORDER: Nao foi possivel confirmar balance - prosseguindo com cautela",
                extra={
                    "event": "pre_order_balance_unknown",
                    "collateral": collateral,
                    "token_balance": token_balance,
                },
            )
        else:
            required_buy = (execution_plan.buy.price * execution_plan.buy.size) + self.settings.min_collateral_buffer
            required_sell = execution_plan.sell.size
            
            logger.info(
                "PRE-ORDER: Balance confirmado",
                extra={
                    "event": "pre_order_balance_confirmed",
                    "collateral": collateral,
                    "required_for_buy": required_buy,
                    "token_balance": token_balance,
                    "required_for_sell": required_sell,
                    "buy_feasible": collateral >= required_buy,
                    "sell_feasible": token_balance >= required_sell,
                },
            )
        
        logger.info(
            "PRE-ORDER: Todas as validacoes completas - pronto para criar ordens",
            extra={"event": "pre_order_validation_ok"},
        )
        
        return True

    # ========== DECISION LOGGING: Log all critical decisions ==========
    def _log_decision(self, decision_type: str, decision: bool, details: dict[str, Any]) -> None:
        """
        Log every critical decision for audit trail.
        CRITICAL FOR DEBUGGING AND COMPLIANCE.
        """
        logger.info(
            f"DECISAO: {decision_type}",
            extra={
                "event": f"decision_{decision_type.lower()}",
                "decision": "ACEITA" if decision else "REJEITADA",
                **details,
            },
        )
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
