from __future__ import annotations

from dataclasses import asdict
from typing import Any

from py_clob_client_v2 import (
    ApiCreds,
    ClobClient,
    OrderArgs,
    OrderType,
    PartialCreateOrderOptions,
    Side,
)

from app.config import Settings


class PolymarketClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = self._build_client()

    def _build_client(self) -> ClobClient:
        if self.settings.api_key and self.settings.api_secret and self.settings.api_passphrase:
            creds = ApiCreds(
                api_key=self.settings.api_key,
                api_secret=self.settings.api_secret,
                api_passphrase=self.settings.api_passphrase,
            )
            return ClobClient(
                host=self.settings.host,
                chain_id=self.settings.chain_id,
                key=self.settings.private_key,
                creds=creds,
            )

        l1_client = ClobClient(
            host=self.settings.host,
            chain_id=self.settings.chain_id,
            key=self.settings.private_key,
        )
        creds = l1_client.create_or_derive_api_key()

        return ClobClient(
            host=self.settings.host,
            chain_id=self.settings.chain_id,
            key=self.settings.private_key,
            creds=creds,
        )

    def get_current_price(self, token_id: str) -> float:
        # Tentativas de compatibilidade entre versões da lib.
        client = self.client

        if hasattr(client, "get_midpoint_price"):
            result = client.get_midpoint_price(token_id=token_id)
            return self._extract_price(result)

        if hasattr(client, "get_price"):
            result = client.get_price(token_id=token_id)
            return self._extract_price(result)

        for getter_name in ("get_book", "get_order_book"):
            if hasattr(client, getter_name):
                book = getattr(client, getter_name)(token_id=token_id)
                return self._mid_from_book(book)

        raise RuntimeError(
            "Unable to fetch current price: no compatible price method found in py-clob-client-v2."
        )

    def place_limit_order(self, token_id: str, side: str, price: float, size: float) -> dict[str, Any]:
        enum_side = Side.BUY if side.lower() == "buy" else Side.SELL

        response = self.client.create_and_post_order(
            order_args=OrderArgs(
                token_id=token_id,
                price=price,
                side=enum_side,
                size=size,
            ),
            options=PartialCreateOrderOptions(tick_size=self.settings.tick_size),
            order_type=OrderType.GTC,
        )
        return self._to_dict(response)

    @staticmethod
    def _to_dict(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            return payload
        if hasattr(payload, "model_dump"):
            return payload.model_dump()
        if hasattr(payload, "dict"):
            return payload.dict()
        if hasattr(payload, "__dataclass_fields__"):
            return asdict(payload)
        return {"raw": str(payload)}

    @staticmethod
    def _extract_price(payload: Any) -> float:
        if isinstance(payload, (int, float, str)):
            return float(payload)

        if isinstance(payload, dict):
            for key in ("price", "mid", "midpoint", "midpoint_price", "value"):
                if key in payload:
                    return float(payload[key])

        for attr in ("price", "mid", "midpoint", "midpoint_price", "value"):
            if hasattr(payload, attr):
                return float(getattr(payload, attr))

        raise RuntimeError(f"Unable to parse price payload: {payload}")

    def _mid_from_book(self, book_payload: Any) -> float:
        book = self._to_dict(book_payload)

        bids = book.get("bids") or []
        asks = book.get("asks") or []
        if not bids or not asks:
            raise RuntimeError(f"Order book missing bids/asks: {book}")

        best_bid = self._best_price(bids, side="bid")
        best_ask = self._best_price(asks, side="ask")
        return (best_bid + best_ask) / 2.0

    @staticmethod
    def _best_price(levels: list[Any], side: str) -> float:
        prices: list[float] = []
        for level in levels:
            if isinstance(level, dict):
                value = level.get("price")
            else:
                value = getattr(level, "price", None)
            if value is not None:
                prices.append(float(value))

        if not prices:
            raise RuntimeError("Unable to parse order book levels")

        return max(prices) if side == "bid" else min(prices)
