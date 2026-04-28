from __future__ import annotations

import json
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.bot import MarketMakerBot, Quote
from app.config import Settings
from app.polymarket import PolymarketApiError, PolymarketClient


@pytest.fixture
def temp_state_file():
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
        json.dump({"known_orders": {}, "last_market_price": None, "consecutive_api_errors": 0, "last_api_error_time": 0.0, "last_api_sync_time": 0.0}, f)
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
        spread=0.02,
        size=10.0,
        loop_interval_seconds=1,
        tick_size="0.01",
        price_tolerance=0.0001,
        min_collateral_buffer=1.0,
        max_balance_usage_pct=0.9,
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
        min_price_bound=0.05,
        max_price_bound=0.95,
        max_book_spread_pct=0.02,
        max_midpoint_deviation_ratio=0.25,
        max_sync_age_seconds=60.0,
        dry_run=False,
    )


@pytest.fixture
def mock_client():
    client = MagicMock(spec=PolymarketClient)
    client.get_open_orders.return_value = []
    client.get_collateral_balance.return_value = 100.0
    client.get_token_balance.return_value = 100.0
    client.get_market_snapshot.return_value = {
        "token_id": "test-token",
        "midpoint": 0.50,
        "book_spread": 0.01,
        "best_bid": 0.495,
        "best_ask": 0.505,
    }
    client.extract_order_id.side_effect = lambda order: order.get("order_id") if isinstance(order, dict) else None
    client.extract_side.side_effect = lambda order: order.get("side", "")
    client.extract_price.side_effect = lambda order: order.get("price")
    client.extract_size.side_effect = lambda order: order.get("size")
    client.extract_status.side_effect = lambda order: order.get("status", "")
    client.place_limit_order.side_effect = lambda **kwargs: {"order_id": f"{kwargs['side']}-1", **kwargs}
    return client


class TestKillSwitch:
    def test_kill_switch_false_when_file_missing(self, settings, mock_client):
        bot = MarketMakerBot(settings, mock_client)
        assert bot._check_kill_switch() is False

    def test_kill_switch_true_when_file_exists(self, settings, mock_client, kill_switch_path):
        Path(kill_switch_path).touch()
        bot = MarketMakerBot(settings, mock_client)
        assert bot._check_kill_switch() is True


class TestFailClosed:
    def test_balance_check_fails_closed_on_none(self, settings, mock_client):
        mock_client.get_collateral_balance.return_value = None
        bot = MarketMakerBot(settings, mock_client)
        with pytest.raises(PolymarketApiError):
            bot._get_confirmed_balances()

    def test_balance_check_fails_below_threshold(self, settings, mock_client):
        mock_client.get_collateral_balance.return_value = 5.0
        bot = MarketMakerBot(settings, mock_client)
        with pytest.raises(PolymarketApiError):
            bot._get_confirmed_balances()

    def test_place_order_rejects_incomplete_quote(self, settings, mock_client):
        bot = MarketMakerBot(settings, mock_client)
        with pytest.raises(PolymarketApiError):
            bot._place_order(Quote(side="buy", price=0.0, size=10.0))

    def test_market_snapshot_fails_on_outlier(self, settings, mock_client):
        bot = MarketMakerBot(settings, mock_client)
        bot.last_market_price = 0.50
        mock_client.get_market_snapshot.return_value = {
            "token_id": "test-token",
            "midpoint": 0.80,
            "book_spread": 0.01,
            "best_bid": 0.795,
            "best_ask": 0.805,
        }
        with pytest.raises(PolymarketApiError):
            bot._get_valid_market_snapshot()


class TestStateAndSync:
    def test_sync_uses_api_as_source_of_truth(self, settings, mock_client):
        mock_client.get_open_orders.return_value = [
            {"order_id": "buy-1", "side": "buy", "price": 0.49, "size": 10.0, "status": "open"},
        ]
        bot = MarketMakerBot(settings, mock_client)
        bot.known_orders = {"stale": {"side": "sell"}}
        bot._sync_orders_with_api()
        assert set(bot.known_orders.keys()) == {"buy-1"}

    def test_validate_state_fails_on_duplicate_side(self, settings, mock_client):
        bot = MarketMakerBot(settings, mock_client)
        bot.known_orders = {
            "1": {"token_id": "test-token", "side": "buy", "price": 0.49, "size": 10.0},
            "2": {"token_id": "test-token", "side": "buy", "price": 0.48, "size": 10.0},
        }
        with pytest.raises(PolymarketApiError):
            bot._validate_state_or_raise()

    def test_stale_sync_forces_refresh(self, settings, mock_client):
        bot = MarketMakerBot(settings, mock_client)
        bot.last_api_sync_time = time.time() - 120
        with patch.object(bot, "_sync_orders_with_api") as sync_mock:
            bot._ensure_fresh_sync()
        sync_mock.assert_called_once()


class TestOrderFlow:
    def test_should_refresh_on_quote_mismatch(self, settings, mock_client):
        bot = MarketMakerBot(settings, mock_client)
        quotes = {"buy": Quote("buy", 0.49, 10.0), "sell": Quote("sell", 0.51, 10.0)}
        open_orders = [{"order_id": "buy-1", "side": "buy", "price": 0.40, "size": 10.0, "status": "open"}]
        assert bot._should_refresh_orders(open_orders, quotes, 0.50) is True

    def test_place_missing_quotes_never_places_without_balance(self, settings, mock_client):
        bot = MarketMakerBot(settings, mock_client)
        quotes = {"buy": Quote("buy", 0.49, 10.0), "sell": Quote("sell", 0.51, 10.0)}
        bot._place_missing_quotes(quotes, collateral=1.0, token_balance=0.0, open_orders=[])
        assert mock_client.place_limit_order.call_count == 0

    def test_place_missing_quotes_places_both_sides_when_balances_confirmed(self, settings, mock_client):
        bot = MarketMakerBot(settings, mock_client)
        quotes = {"buy": Quote("buy", 0.49, 10.0), "sell": Quote("sell", 0.51, 10.0)}
        with patch.object(bot, "_sync_orders_with_api") as sync_mock:
            bot._place_missing_quotes(quotes, collateral=100.0, token_balance=100.0, open_orders=[])
        assert mock_client.place_limit_order.call_count == 2
        assert sync_mock.call_count == 2

    def test_dry_run_skips_order_placement(self, settings, mock_client):
        dry_settings = replace(settings, dry_run=True)
        bot = MarketMakerBot(dry_settings, mock_client)
        bot._place_order(Quote("buy", 0.49, 10.0))
        assert mock_client.place_limit_order.call_count == 0

    def test_dry_run_skips_cancellation(self, settings, mock_client):
        dry_settings = replace(settings, dry_run=True)
        bot = MarketMakerBot(dry_settings, mock_client)
        bot.cancel_open_orders([{"order_id": "buy-1", "side": "buy", "price": 0.49, "size": 10.0, "status": "open"}])
        assert mock_client.cancel_orders.call_count == 0


class TestRunLoop:
    def test_shutdown_event_initialized(self, settings, mock_client):
        bot = MarketMakerBot(settings, mock_client)
        assert isinstance(bot.shutdown_event, threading.Event)
        assert not bot.shutdown_event.is_set()

    def test_loop_stops_after_keyboard_interrupt(self, settings, mock_client):
        bot = MarketMakerBot(settings, mock_client)
        with patch.object(bot, "run_once", side_effect=KeyboardInterrupt()):
            with patch("time.sleep") as sleep_mock:
                bot.run_forever()
        assert bot.shutdown_event.is_set()
        sleep_mock.assert_not_called()

    def test_loop_stops_after_repeated_api_errors(self, settings, mock_client):
        stricter_settings = replace(settings, max_api_failure_streak=1)
        bot = MarketMakerBot(stricter_settings, mock_client)
        with patch.object(bot, "run_once", side_effect=PolymarketApiError("boom")):
            with patch("time.sleep"):
                bot.run_forever()
        assert bot.shutdown_event.is_set()

    def test_request_shutdown_sets_event(self, settings, mock_client):
        bot = MarketMakerBot(settings, mock_client)
        bot.request_shutdown("sigterm")
        assert bot.shutdown_event.is_set()
