from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.polymarket import OPEN_STATUSES, PolymarketApiError, PolymarketClient


@dataclass
class DummySettings:
    host: str = "https://clob.polymarket.com"
    chain_id: int = 137
    private_key: str = "key"
    tick_size: str = "0.01"
    api_key: str | None = None
    api_secret: str | None = None
    api_passphrase: str | None = None


def build_client() -> PolymarketClient:
    """Build a PolymarketClient with mocked internal state for testing."""
    client = PolymarketClient.__new__(PolymarketClient)
    client.settings = DummySettings()  # type: ignore
    client.client = None  # type: ignore
    return client


class TestPolymarketWrapper:
    def test_extract_open_orders_filters_non_open_statuses(self):
        """Test that get_open_orders filters orders by open status."""
        client = build_client()

        class FakeApi:
            def get_orders(self, token_id: str):
                return {
                    "orders": [
                        {"id": "1", "status": "open", "price": 0.49, "size": 10},
                        {"id": "2", "status": "cancelled", "price": 0.51, "size": 10},
                    ]
                }

        client.client = FakeApi()  # type: ignore
        orders = client.get_open_orders("token")
        assert len(orders) == 1, f"Expected 1 open order, got {len(orders)}"
        assert orders[0]["id"] == "1", f"Expected order id '1', got {orders[0]['id']}"
        assert orders[0]["status"] == "open", f"Expected status 'open', got {orders[0]['status']}"

    def test_get_market_snapshot_raises_without_bid_ask(self):
        """Test that get_market_snapshot raises when bids or asks are missing."""
        client = build_client()

        class FakeApi:
            def get_book(self, token_id: str):
                return {"bids": [], "asks": []}

        client.client = FakeApi()  # type: ignore
        with pytest.raises(PolymarketApiError, match="Order book missing bids/asks"):
            client.get_market_snapshot("token")

    def test_extract_balance_supports_scalar_and_dict(self):
        """Test that _extract_balance handles both scalar values and dictionaries."""
        # Test with integer
        balance_int = PolymarketClient._extract_balance(10)
        assert balance_int == 10.0, f"Expected 10.0, got {balance_int}"
        
        # Test with float
        balance_float = PolymarketClient._extract_balance(25.5)
        assert balance_float == 25.5, f"Expected 25.5, got {balance_float}"
        
        # Test with dictionary
        balance_dict = PolymarketClient._extract_balance({"available": "12.5"})
        assert balance_dict == 12.5, f"Expected 12.5, got {balance_dict}"
        
        # Test with None
        balance_none = PolymarketClient._extract_balance(None)
        assert balance_none is None, f"Expected None, got {balance_none}"

    def test_call_client_wraps_unexpected_errors(self):
        """Test that unexpected errors from client methods are wrapped in PolymarketApiError."""
        client = build_client()

        class FakeApi:
            def get_orders(self, token_id: str):
                raise RuntimeError("boom")

        client.client = FakeApi()  # type: ignore
        with pytest.raises(PolymarketApiError):
            client.get_open_orders("token")
