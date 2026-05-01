from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.market_data import PolymarketMarketStream


@pytest.fixture
def stream_settings():
    return SimpleNamespace(
        token_id="test-token",
        ws_market_url="wss://example.test/ws",
        ws_reconnect_seconds=0.01,
    )


def test_market_stream_builds_snapshot_from_book(stream_settings):
    settings = stream_settings
    stream = PolymarketMarketStream(settings)
    changed = stream._apply_message(
        {
            "event_type": "book",
            "asset_id": "test-token",
            "bids": [{"price": "0.49", "size": "100"}, {"price": "0.48", "size": "50"}],
            "asks": [{"price": "0.51", "size": "100"}, {"price": "0.52", "size": "50"}],
        }
    )

    snapshot = stream.latest_snapshot()

    assert changed is True
    assert snapshot is not None
    assert snapshot["best_bid"] == pytest.approx(0.49)
    assert snapshot["best_ask"] == pytest.approx(0.51)
    assert snapshot["midpoint"] == pytest.approx(0.50)
    assert snapshot["source"] == "websocket"


def test_market_stream_applies_price_changes(stream_settings):
    settings = stream_settings
    stream = PolymarketMarketStream(settings)
    stream._apply_message(
        {
            "event_type": "book",
            "asset_id": "test-token",
            "bids": [{"price": "0.49", "size": "100"}],
            "asks": [{"price": "0.51", "size": "100"}, {"price": "0.52", "size": "50"}],
        }
    )
    stream._apply_message(
        {
            "event_type": "price_change",
            "price_changes": [
                {"asset_id": "test-token", "side": "BUY", "price": "0.50", "size": "25"},
                {"asset_id": "test-token", "side": "SELL", "price": "0.51", "size": "0"},
                {"asset_id": "other-token", "side": "BUY", "price": "0.99", "size": "1"},
            ],
        }
    )

    snapshot = stream.latest_snapshot()

    assert snapshot is not None
    assert snapshot["best_bid"] == pytest.approx(0.50)
    assert snapshot["best_ask"] == pytest.approx(0.52)
