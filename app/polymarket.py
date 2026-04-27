from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

OPEN_STATUSES = {"open", "live", "active", "resting", "unfilled", "pending"}
FILLED_STATUSES = {"filled", "matched", "executed", "complete", "completed"}
CLOSED_STATUSES = FILLED_STATUSES | {"canceled", "cancelled", "expired", "rejected"}


class PolymarketApiError(RuntimeError):
    """Erro de integração com a API Polymarket CLOB."""


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
        creds = self._call_client(l1_client, "create_or_derive_api_key")

        return ClobClient(
            host=self.settings.host,
            chain_id=self.settings.chain_id,
            key=self.settings.private_key,
            creds=creds,
        )

    def get_current_price(self, token_id: str) -> float:
        if hasattr(self.client, "get_midpoint_price"):
            return self._extract_price(self._call_client(self.client, "get_midpoint_price", token_id=token_id))

        if hasattr(self.client, "get_price"):
            return self._extract_price(self._call_client(self.client, "get_price", token_id=token_id))

        for getter_name in ("get_book", "get_order_book"):
            if hasattr(self.client, getter_name):
                book = self._call_client(self.client, getter_name, token_id=token_id)
                return self._mid_from_book(book)

        raise PolymarketApiError("Unable to fetch current price with current py-clob-client-v2 version.")

    def get_order_book(self, token_id: str) -> dict[str, Any]:
        for getter_name in ("get_book", "get_order_book"):
            if hasattr(self.client, getter_name):
                book = self._call_client(self.client, getter_name, token_id=token_id)
                return self._to_dict(book)
        raise PolymarketApiError("Unable to fetch order book with current py-clob-client-v2 version.")

    def get_market_snapshot(self, token_id: str) -> dict[str, Any]:
        book = self.get_order_book(token_id)
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        if not bids or not asks:
            raise PolymarketApiError(f"Order book missing bids/asks: {book}")

        best_bid = self._best_price(bids, side="bid")
        best_ask = self._best_price(asks, side="ask")
        bid_depth = self._sum_sizes(bids)
        ask_depth = self._sum_sizes(asks)
        midpoint = (best_bid + best_ask) / 2.0
        liquidity_score = min(bid_depth, ask_depth)
        return {
            "token_id": token_id,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "midpoint": midpoint,
            "book_spread": max(0.0, best_ask - best_bid),
            "bid_depth": bid_depth,
            "ask_depth": ask_depth,
            "liquidity_score": liquidity_score,
            "raw_book": book,
        }

    def get_candidate_token_ids(self) -> list[str]:
        token_ids = list(dict.fromkeys([self.settings.token_id, *self.settings.token_ids]))
        discovered = self._discover_token_ids()
        for token_id in discovered:
            if token_id not in token_ids:
                token_ids.append(token_id)
        return token_ids[: self.settings.market_scan_limit]

    def get_open_orders(self, token_id: str) -> list[dict[str, Any]]:
        candidates = [
            ("get_open_orders", {"token_id": token_id}),
            ("get_orders", {"token_id": token_id}),
            ("get_orders", {"market": token_id}),
            ("list_orders", {"token_id": token_id}),
            ("list_open_orders", {"token_id": token_id}),
        ]

        for method_name, kwargs in candidates:
            if not hasattr(self.client, method_name):
                continue
            payload = self._call_client(self.client, method_name, **kwargs)
            raw_orders = self._extract_list(payload, keys=("orders", "data", "items", "results"))
            orders = [self._to_dict(item) for item in raw_orders]
            if method_name in {"get_open_orders", "list_open_orders"}:
                return orders
            return [o for o in orders if self._extract_status(o) in OPEN_STATUSES]

        return []

    def cancel_orders(self, order_ids: list[str]) -> dict[str, Any]:
        if not order_ids:
            return {"cancelled": [], "raw": None}

        for method_name, key_name in (
            ("cancel_orders", "order_ids"),
            ("cancel_orders", "ids"),
            ("cancel", "order_ids"),
            ("batch_cancel_orders", "order_ids"),
        ):
            if hasattr(self.client, method_name):
                payload = self._call_client(self.client, method_name, **{key_name: order_ids})
                return {"cancelled": order_ids, "raw": self._to_dict(payload)}

        if hasattr(self.client, "cancel_order"):
            raw: list[dict[str, Any]] = []
            for order_id in order_ids:
                raw.append(self._to_dict(self._call_client(self.client, "cancel_order", order_id=order_id)))
            return {"cancelled": order_ids, "raw": raw}

        raise PolymarketApiError("Cancel method not available in current py-clob-client-v2 version.")

    def get_order_details(self, order_id: str) -> dict[str, Any] | None:
        for method_name in ("get_order", "get_order_by_id"):
            if hasattr(self.client, method_name):
                payload = self._call_client(self.client, method_name, order_id=order_id)
                return self._to_dict(payload)
        return None

    def get_collateral_balance(self) -> float | None:
        candidates = [
            ("get_collateral_balance", {}),
            ("get_balance", {}),
            ("get_usdc_balance", {}),
        ]
        for method_name, kwargs in candidates:
            if hasattr(self.client, method_name):
                payload = self._call_client(self.client, method_name, **kwargs)
                return self._extract_balance(payload)
        return None

    def get_token_balance(self, token_id: str) -> float | None:
        for method_name in ("get_token_balance", "get_balance"):
            if not hasattr(self.client, method_name):
                continue
            payload = self._call_client(self.client, method_name, token_id=token_id)
            extracted = self._extract_balance(payload)
            if extracted is not None:
                return extracted

        if hasattr(self.client, "get_positions"):
            payload = self._call_client(self.client, "get_positions")
            positions = self._extract_list(payload, keys=("positions", "data", "items"))
            for pos in positions:
                parsed = self._to_dict(pos)
                if str(parsed.get("token_id")) == str(token_id):
                    return self._extract_balance(parsed)

        return None

    def place_limit_order(self, token_id: str, side: str, price: float, size: float) -> dict[str, Any]:
        enum_side = Side.BUY if side.lower() == "buy" else Side.SELL
        response = self._call_client(
            self.client,
            "create_and_post_order",
            order_args=OrderArgs(token_id=token_id, price=price, side=enum_side, size=size),
            options=PartialCreateOrderOptions(tick_size=self.settings.tick_size),
            order_type=OrderType.GTC,
        )
        return self._to_dict(response)

    def _call_client(self, target: Any, method_name: str, **kwargs: Any) -> Any:
        try:
            method = getattr(target, method_name)
            result = method(**kwargs)
            
            # FAIL-SAFE: Validate API response is not None/empty for critical methods
            if method_name in {
                "get_open_orders",
                "get_orders",
                "list_orders",
                "list_open_orders",
                "get_collateral_balance",
                "get_token_balance",
            }:
                if result is None:
                    logger.warning(
                        "API response nula para metodo critico",
                        extra={"event": "api_null_response", "method": method_name},
                    )
                    raise PolymarketApiError(f"API returned None for {method_name}")
            
            return result
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Falha na chamada CLOB",
                extra={
                    "method": method_name,
                    "params": kwargs,
                    "error_type": type(exc).__name__,
                },
            )
            raise PolymarketApiError(f"CLOB call failed: {method_name}") from exc

    def _discover_token_ids(self) -> list[str]:
        candidates = [
            ("get_markets", {}),
            ("list_markets", {}),
            ("get_simplified_markets", {}),
            ("get_sampling_markets", {}),
        ]
        for method_name, kwargs in candidates:
            if not hasattr(self.client, method_name):
                continue
            try:
                payload = self._call_client(self.client, method_name, **kwargs)
            except PolymarketApiError:
                continue

            raw_items = self._extract_list(payload, keys=("markets", "data", "items", "results"))
            token_ids = self._extract_token_ids_from_markets(raw_items)
            if token_ids:
                return token_ids
        return []

    @staticmethod
    def _extract_list(payload: Any, keys: tuple[str, ...]) -> list[Any]:
        if isinstance(payload, list):
            return payload

        normalized = PolymarketClient._to_dict(payload)
        for key in keys:
            if key in normalized and isinstance(normalized[key], list):
                return normalized[key]
        return []

    @staticmethod
    def _extract_token_ids_from_markets(markets: list[Any]) -> list[str]:
        token_ids: list[str] = []
        for market in markets:
            parsed = PolymarketClient._to_dict(market)
            for key in ("token_id", "tokenId", "clob_token_id", "clobTokenId"):
                value = parsed.get(key)
                if value:
                    token_ids.append(str(value))
            outcomes = parsed.get("outcomes") or parsed.get("tokens") or parsed.get("market_tokens") or []
            if isinstance(outcomes, list):
                for outcome in outcomes:
                    outcome_dict = PolymarketClient._to_dict(outcome)
                    for key in ("token_id", "tokenId", "clob_token_id", "clobTokenId"):
                        value = outcome_dict.get(key)
                        if value:
                            token_ids.append(str(value))
        return list(dict.fromkeys(token_ids))

    @staticmethod
    def _extract_status(order: dict[str, Any]) -> str:
        for key in ("status", "state", "order_status"):
            if key in order and order[key] is not None:
                return str(order[key]).lower()
        return ""

    @staticmethod
    def extract_order_id(order: dict[str, Any]) -> str | None:
        for key in ("order_id", "id", "hash", "orderHash"):
            value = order.get(key)
            if value:
                return str(value)
        return None

    @staticmethod
    def _extract_balance(payload: Any) -> float | None:
        if payload is None:
            return None
        if isinstance(payload, (int, float, str)):
            return float(payload)

        normalized = PolymarketClient._to_dict(payload)
        for key in ("available", "balance", "amount", "free", "value", "size"):
            value = normalized.get(key)
            if value is not None:
                return float(value)
        return None

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

        raise PolymarketApiError(f"Unable to parse price payload: {payload}")

    def _mid_from_book(self, book_payload: Any) -> float:
        book = self._to_dict(book_payload)

        bids = book.get("bids") or []
        asks = book.get("asks") or []
        if not bids or not asks:
            raise PolymarketApiError(f"Order book missing bids/asks: {book}")

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
            raise PolymarketApiError("Unable to parse order book levels")

        return max(prices) if side == "bid" else min(prices)

    @staticmethod
    def _sum_sizes(levels: list[Any]) -> float:
        total = 0.0
        for level in levels:
            if isinstance(level, dict):
                value = level.get("size") or level.get("quantity") or level.get("amount")
            else:
                value = (
                    getattr(level, "size", None)
                    or getattr(level, "quantity", None)
                    or getattr(level, "amount", None)
                )
            if value is not None:
                total += float(value)
        return total
