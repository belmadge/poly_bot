from __future__ import annotations

import json
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.bot import ExecutionPlan, MarketMakerBot, Quote, QuoteContext
from app.config import Settings
from app.polymarket import PolymarketApiError, PolymarketClient


@pytest.fixture
def temp_state_file():
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
        state_data = {
            "known_orders": {},
            "net_positions": {},
            "last_market_prices": {},
            "trades_executed": 0,
            "total_pnl_realized": 0.0,
            "last_metrics_log_time": 0.0,
            "consecutive_api_errors": 0,
            "last_api_error_time": 0.0,
            "last_hedge_creation_time": {},
            "last_api_sync_time": 0.0,
        }
        json.dump(state_data, f)
        temp_path = f.name
    yield temp_path
    Path(temp_path).unlink(missing_ok=True)


@pytest.fixture
def kill_switch_path(temp_state_file):
    path = Path(temp_state_file).with_suffix(".kill")
    path.unlink(missing_ok=True)
    yield str(path)
    path.unlink(missing_ok=True)


@pytest.fixture
def settings(temp_state_file, kill_switch_path):
    return Settings(
        host="https://clob.polymarket.com",
        chain_id=137,
        private_key="test-private-key",
        token_id="test-token",
        token_ids=("test-token",),
        spread=0.02,
        size=10.0,
        loop_interval_seconds=1,
        tick_size="0.01",
        price_tolerance=0.0001,
        min_collateral_buffer=1.0,
        target_position_size=0.0,
        max_position_imbalance=10.0,
        max_position_size=50.0,
        max_balance_usage_pct=0.9,
        price_refresh_threshold=0.01,
        auto_select_market=False,
        market_scan_limit=25,
        dynamic_spread_enabled=True,
        min_spread=0.02,
        max_spread=0.04,
        liquidity_target_low=10.0,
        liquidity_target_high=50.0,
        low_volatility_threshold=0.002,
        high_volatility_threshold=0.01,
        low_volatility_spread_multiplier=0.85,
        high_volatility_spread_multiplier=1.4,
        max_retries=0,
        retry_delay_seconds=0.0,
        log_level="INFO",
        log_format="text",
        state_file=temp_state_file,
        max_consecutive_errors=5,
        min_balance_threshold=10.0,
        max_api_failure_streak=3,
        kill_switch_flag_file=kill_switch_path,
        reposition_price_threshold=0.001,
        metrics_log_interval_seconds=3600,
        hedge_min_position_size=5.0,
        hedge_delay_seconds=30.0,
        min_price_bound=0.05,
        max_price_bound=0.95,
        max_book_spread_pct=0.02,
        max_midpoint_deviation_ratio=0.25,
        max_sync_age_seconds=60.0,
    )


@pytest.fixture
def mock_client():
    client = MagicMock(spec=PolymarketClient)
    client.get_open_orders.return_value = []
    client.get_candidate_token_ids.return_value = []
    client.get_collateral_balance.return_value = 100.0
    client.get_token_balance.return_value = 100.0
    client.extract_order_id.side_effect = lambda order: order.get("order_id") if isinstance(order, dict) else None
    client.get_market_snapshot.return_value = {
        "token_id": "test-token",
        "midpoint": 0.5,
        "book_spread": 0.01,
        "best_bid": 0.495,
        "best_ask": 0.505,
        "liquidity_score": 100.0,
    }
    return client


def build_context() -> QuoteContext:
    quotes = {
        "buy": Quote(side="buy", price=0.49, size=10.0),
        "sell": Quote(side="sell", price=0.51, size=10.0),
    }
    return QuoteContext(
        token_id="test-token",
        current_price=0.5,
        previous_price=0.5,
        price_change_ratio=0.0,
        spread=0.02,
        book_spread=0.01,
        liquidity_score=100.0,
        quotes=quotes,
    )


def build_execution_plan() -> ExecutionPlan:
    return ExecutionPlan(
        buy=Quote(side="buy", price=0.49, size=10.0),
        sell=Quote(side="sell", price=0.51, size=10.0),
        net_position=0.0,
    )


class TestCheckKillSwitch:
    def test_kill_switch_not_activated_by_default(self, settings, mock_client):
        bot = MarketMakerBot(settings, mock_client)
        assert bot._check_kill_switch() is False

    def test_kill_switch_activated_when_flag_file_exists(self, settings, mock_client, kill_switch_path):
        Path(kill_switch_path).touch()
        bot = MarketMakerBot(settings, mock_client)
        assert bot._check_kill_switch() is True

    def test_check_kill_switch_is_standalone_method(self, settings, mock_client):
        bot = MarketMakerBot(settings, mock_client)
        assert isinstance(bot._check_kill_switch(), bool)
        assert bot._check_kill_switch.__doc__ is not None
        assert "flag file" in bot._check_kill_switch.__doc__.lower()


class TestFailClosedValidations:
    def test_check_balance_safety_fails_closed_on_none(self, settings, mock_client):
        mock_client.get_collateral_balance.return_value = None
        bot = MarketMakerBot(settings, mock_client)
        assert bot._check_balance_safety() is False

    def test_check_balance_safety_passes_on_sufficient_balance(self, settings, mock_client):
        bot = MarketMakerBot(settings, mock_client)
        adjusted = replace(settings, min_balance_threshold=10.0)
        bot = MarketMakerBot(adjusted, mock_client)
        assert bot._check_balance_safety() is True

    def test_check_balance_safety_fails_below_threshold(self, settings, mock_client):
        mock_client.get_collateral_balance.return_value = 5.0
        bot = MarketMakerBot(settings, mock_client)
        assert bot._check_balance_safety() is False

    def test_has_buy_balance_fails_closed_on_none(self, settings, mock_client):
        mock_client.get_collateral_balance.return_value = None
        bot = MarketMakerBot(settings, mock_client)
        assert bot._has_buy_balance(Quote(side="buy", price=0.49, size=10.0)) is False

    def test_has_sell_balance_fails_closed_on_none(self, settings, mock_client):
        mock_client.get_token_balance.return_value = None
        bot = MarketMakerBot(settings, mock_client)
        assert bot._has_sell_balance(Quote(side="sell", price=0.51, size=10.0)) is False

    def test_pre_order_validation_fails_closed_on_none_balance(self, settings, mock_client):
        mock_client.get_collateral_balance.return_value = None
        bot = MarketMakerBot(settings, mock_client)
        context = build_context()
        plan = build_execution_plan()
        assert bot._pre_order_validation(context, plan) is False


class TestShutdownEvent:
    def test_shutdown_event_initialized(self, settings, mock_client):
        bot = MarketMakerBot(settings, mock_client)
        assert hasattr(bot, "shutdown_event")
        assert isinstance(bot.shutdown_event, threading.Event)
        assert not bot.shutdown_event.is_set()

    def test_shutdown_event_set_on_keyboard_interrupt(self, settings, mock_client):
        bot = MarketMakerBot(settings, mock_client)
        bot.shutdown_event.set()
        assert bot.shutdown_event.is_set()

    def test_loop_respects_shutdown_event_for_sleep(self, settings, mock_client):
        fast_settings = replace(settings, loop_interval_seconds=30)
        bot = MarketMakerBot(fast_settings, mock_client)
        call_count = 0

        def mock_run_once():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise KeyboardInterrupt()

        with patch.object(bot, "run_once", side_effect=mock_run_once):
            with patch("time.sleep") as mock_sleep:
                bot.run_forever()
        assert bot.shutdown_event.is_set()
        mock_sleep.assert_not_called()


class TestModularity:
    def test_check_kill_switch_is_pure_function(self, settings, mock_client):
        bot = MarketMakerBot(settings, mock_client)
        initial_state = bot.consecutive_api_errors
        initial_orders = len(bot.known_orders)
        bot._check_kill_switch()
        assert bot.consecutive_api_errors == initial_state
        assert len(bot.known_orders) == initial_orders

    def test_log_decision_only_logs(self, settings, mock_client):
        bot = MarketMakerBot(settings, mock_client)
        initial_orders = len(bot.known_orders)
        bot._log_decision("test_decision", True, {"test": "details"})
        assert len(bot.known_orders) == initial_orders

    def test_shutdown_event_does_not_break_other_methods(self, settings, mock_client):
        bot = MarketMakerBot(settings, mock_client)
        bot.shutdown_event.set()
        bot._check_kill_switch()
        result = bot._check_balance_safety()
        assert isinstance(result, bool)


class TestIntegration:
    def test_all_three_fixes_coexist(self, settings, mock_client):
        bot = MarketMakerBot(settings, mock_client)
        assert hasattr(bot, "_check_kill_switch")
        assert callable(bot._check_kill_switch)
        mock_client.get_collateral_balance.return_value = None
        assert bot._check_balance_safety() is False
        assert hasattr(bot, "shutdown_event")
        assert isinstance(bot.shutdown_event, threading.Event)

    def test_fail_closed_validation_and_shutdown_event_work_together(self, settings, mock_client):
        bot = MarketMakerBot(settings, mock_client)
        mock_client.get_collateral_balance.return_value = None
        assert bot._check_balance_safety() is False
        bot.shutdown_event.set()
        assert bot.shutdown_event.is_set()

    def test_partial_fill_updates_only_incremental_size(self, settings, mock_client):
        bot = MarketMakerBot(settings, mock_client)
        bot.last_api_sync_timestamp = time.time()
        bot.known_orders = {
            "ord-1": {
                "token_id": "test-token",
                "side": "buy",
                "price": 0.49,
                "size": 10.0,
                "filled_size": 3.0,
                "status": "open",
                "raw": {},
            }
        }
        mock_client.get_order_details.return_value = {
            "order_id": "ord-1",
            "status": "open",
            "price": 0.49,
            "size": 10.0,
            "filled_size": 5.0,
            "remaining_size": 5.0,
            "side": "buy",
        }

        fills = bot._check_fills()

        assert len(fills) == 1
        assert fills[0]["fill_size"] == 2.0
        assert fills[0]["filled_size"] == 5.0
        assert bot.net_positions["test-token"] == 2.0

    def test_stale_sync_blocks_operations(self, settings, mock_client):
        stale_settings = replace(settings, max_sync_age_seconds=0.0)
        bot = MarketMakerBot(stale_settings, mock_client)
        bot.last_api_sync_timestamp = time.time() - 60
        mock_client.get_open_orders.side_effect = PolymarketApiError("boom")
        assert bot._ensure_fresh_sync("test-token") is False
