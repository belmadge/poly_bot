from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from py_clob_client_v2 import ApiCreds, ClobClient, OrderArgs, OrderType, PartialCreateOrderOptions, Side

from app.config import Settings

logger = logging.getLogger(__name__)

OPEN_STATUSES = {"open", "live", "active", "resting", "unfilled", "pending"}
CLOSED_STATUSES = {"filled", "matched", "executed", "complete", "completed", "canceled", "cancelled", "expired", "rejected"}


class PolymarketApiError(RuntimeError):
    """Erro de integracao com a API Polymarket CLOB."""


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

    def get_order_book(self, token_id: str) -> dict[str, Any]:
        for method_name in ("get_book", "get_order_book"):
            if hasattr(self.client, method_name):
                return self._to_dict(self._call_client(self.client, method_name, token_id=token_id))
        raise PolymarketApiError("Unable to fetch order book with current py-clob-client-v2 version.")

    def get_market_snapshot(self, token_id: str) -> dict[str, Any]:
        book = self.get_order_book(token_id)
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        if not bids or not asks:
            raise PolymarketApiError(f"Order book missing bids/asks for token {token_id}")

        best_bid = self._best_price(bids, side="bid")
        best_ask = self._best_price(asks, side="ask")
        midpoint = (best_bid + best_ask) / 2.0
        return {
            "token_id": token_id,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "midpoint": midpoint,
            "book_spread": max(0.0, best_ask - best_bid),
            "raw_book": book,
        }

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
            orders = [self._to_dict(item) for item in self._extract_list(payload, ("orders", "data", "items", "results"))]
            if method_name in {"get_open_orders", "list_open_orders"}:
                return orders
            return [order for order in orders if self._extract_status(order) in OPEN_STATUSES]
        return []

    def get_collateral_balance(self) -> float | None:
        for method_name in ("get_collateral_balance", "get_balance", "get_usdc_balance"):
            if hasattr(self.client, method_name):
                return self._extract_balance(self._call_client(self.client, method_name))
        return None

    def get_token_balance(self, token_id: str) -> float | None:
        for method_name in ("get_token_balance", "get_balance"):
            if not hasattr(self.client, method_name):
                continue
            extracted = self._extract_balance(self._call_client(self.client, method_name, token_id=token_id))
            if extracted is not None:
                return extracted

        if hasattr(self.client, "get_positions"):
            payload = self._call_client(self.client, "get_positions")
            for pos in self._extract_list(payload, ("positions", "data", "items")):
                parsed = self._to_dict(pos)
                if str(parsed.get("token_id")) == str(token_id):
                    return self._extract_balance(parsed)
        return None

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
            raw = [self._to_dict(self._call_client(self.client, "cancel_order", order_id=order_id)) for order_id in order_ids]
            return {"cancelled": order_ids, "raw": raw}

        raise PolymarketApiError("Cancel method not available in current py-clob-client-v2 version.")

    def get_order_details(self, order_id: str) -> dict[str, Any] | None:
        for method_name in ("get_order", "get_order_by_id"):
            if hasattr(self.client, method_name):
                return self._to_dict(self._call_client(self.client, method_name, order_id=order_id))
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
            if method_name in {"get_open_orders", "get_orders", "list_orders", "list_open_orders", "get_collateral_balance", "get_token_balance"} and result is None:
                raise PolymarketApiError(f"API returned None for {method_name}")
            return result
        except PolymarketApiError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Falha na chamada CLOB",
                extra={"event": "clob_call_failed", "method": method_name, "params": kwargs, "error_type": type(exc).__name__},
            )
            raise PolymarketApiError(f"CLOB call failed: {method_name}") from exc

    @staticmethod
    def _extract_list(payload: Any, keys: tuple[str, ...]) -> list[Any]:
        if isinstance(payload, list):
            return payload
        normalized = PolymarketClient._to_dict(payload)
        for key in keys:
            value = normalized.get(key)
            if isinstance(value, list):
                return value
        return []

    @staticmethod
    def extract_order_id(order: dict[str, Any]) -> str | None:
        for key in ("order_id", "id", "hash", "orderHash"):
            value = order.get(key)
            if value:
                return str(value)
        return None

    @staticmethod
    def extract_price(order: dict[str, Any]) -> float | None:
        for key in ("price", "limit_price"):
            value = order.get(key)
            if value is not None:
                return float(value)
        return None

    @staticmethod
    def extract_size(order: dict[str, Any]) -> float | None:
        for key in ("size", "quantity", "original_size"):
            value = order.get(key)
            if value is not None:
                return float(value)
        return None

    @staticmethod
    def extract_side(order: dict[str, Any]) -> str:
        value = order.get("side")
        if value is None:
            return ""
        side = str(value).lower()
        if side in {"buy", "bid"}:
            return "buy"
        if side in {"sell", "ask"}:
            return "sell"
        return ""

    @staticmethod
    def extract_status(order: dict[str, Any]) -> str:
        return PolymarketClient._extract_status(order)

    @staticmethod
    def extract_filled_size(order: dict[str, Any]) -> float | None:
        for key in ("filled_size", "filled", "matched_size", "executed_size", "size_matched"):
            value = order.get(key)
            if value is not None:
                return float(value)
        return None

    @staticmethod
    def _extract_status(order: dict[str, Any]) -> str:
        for key in ("status", "state", "order_status"):
            value = order.get(key)
            if value is not None:
                return str(value).lower()
        return ""

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
