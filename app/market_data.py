from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from app.config import Settings

logger = logging.getLogger(__name__)


class MarketDataStreamUnavailable(RuntimeError):
    """Raised when the optional websocket client dependency is unavailable."""


class PolymarketMarketStream:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._update_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._ws_app: Any = None
        self._bids: dict[float, float] = {}
        self._asks: dict[float, float] = {}
        self._last_update_time = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="polymarket-market-ws", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._ws_app is not None:
            try:
                self._ws_app.close()
            except Exception:  # noqa: BLE001
                logger.debug("Failed to close websocket cleanly", extra={"event": "ws_close_failed"})
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def wait_for_update(self, timeout: float) -> bool:
        updated = self._update_event.wait(timeout)
        if updated:
            self._update_event.clear()
        return updated

    def latest_snapshot(self, max_age_seconds: float | None = None) -> dict[str, Any] | None:
        with self._lock:
            if not self._bids or not self._asks:
                return None
            now = time.time()
            if max_age_seconds is not None and now - self._last_update_time > max_age_seconds:
                return None
            bids = [{"price": price, "size": size} for price, size in sorted(self._bids.items(), reverse=True)]
            asks = [{"price": price, "size": size} for price, size in sorted(self._asks.items())]

        best_bid = bids[0]["price"]
        best_ask = asks[0]["price"]
        return {
            "token_id": self.settings.token_id,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "midpoint": (best_bid + best_ask) / 2.0,
            "book_spread": max(0.0, best_ask - best_bid),
            "raw_book": {"bids": bids, "asks": asks},
            "source": "websocket",
        }

    def _run(self) -> None:
        try:
            import websocket
        except ImportError as exc:
            raise MarketDataStreamUnavailable("Install websocket-client to enable ENABLE_WEBSOCKET=true") from exc

        while not self._stop_event.is_set():
            self._ws_app = websocket.WebSocketApp(
                self.settings.ws_market_url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            self._ws_app.run_forever(ping_interval=20, ping_timeout=10)
            if not self._stop_event.is_set():
                time.sleep(self.settings.ws_reconnect_seconds)

    def _on_open(self, ws: Any) -> None:
        payload = {
            "assets_ids": [self.settings.token_id],
            "type": "market",
            "custom_feature_enabled": True,
        }
        ws.send(json.dumps(payload))
        logger.info("Market websocket subscribed", extra={"event": "ws_subscribed", "token_id": self.settings.token_id})

    def _on_message(self, _ws: Any, message: str) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            logger.warning("Ignoring invalid websocket payload", extra={"event": "ws_invalid_json"})
            return

        messages = payload if isinstance(payload, list) else [payload]
        changed = False
        for item in messages:
            if isinstance(item, dict):
                changed = self._apply_message(item) or changed
        if changed:
            self._update_event.set()

    def _apply_message(self, payload: dict[str, Any]) -> bool:
        event_type = payload.get("event_type")
        asset_id = str(payload.get("asset_id") or "")
        if asset_id and asset_id != str(self.settings.token_id):
            return False

        if event_type == "book":
            with self._lock:
                self._bids = self._levels_by_price(payload.get("bids") or [])
                self._asks = self._levels_by_price(payload.get("asks") or [])
                self._last_update_time = time.time()
            return True

        if event_type == "price_change":
            changed = False
            for change in payload.get("price_changes") or []:
                if str(change.get("asset_id") or "") != str(self.settings.token_id):
                    continue
                side = str(change.get("side") or "").lower()
                price = self._parse_float(change.get("price"))
                size = self._parse_float(change.get("size"))
                if side and price is not None and size is not None:
                    self._update_level(side, price, size)
                    changed = True
            return changed

        if event_type in {"last_trade_price", "best_bid_ask"}:
            return True
        if event_type == "market_resolved":
            logger.critical("Subscribed market resolved", extra={"event": "market_resolved", "token_id": self.settings.token_id})
            return True
        return False

    def _update_level(self, side: str, price: float, size: float) -> None:
        book_side = self._bids if side in {"buy", "bid"} else self._asks
        with self._lock:
            if size <= 0:
                book_side.pop(price, None)
            else:
                book_side[price] = size
            self._last_update_time = time.time()

    @classmethod
    def _levels_by_price(cls, levels: list[Any]) -> dict[float, float]:
        parsed: dict[float, float] = {}
        for level in levels:
            if not isinstance(level, dict):
                continue
            price = cls._parse_float(level.get("price"))
            size = cls._parse_float(level.get("size") or level.get("quantity"))
            if price is not None and size is not None and size > 0:
                parsed[price] = size
        return parsed

    @staticmethod
    def _parse_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _on_error(self, _ws: Any, error: Any) -> None:
        logger.warning("Market websocket error", extra={"event": "ws_error", "error": str(error)})

    def _on_close(self, _ws: Any, status_code: Any, message: Any) -> None:
        logger.info("Market websocket closed", extra={"event": "ws_closed", "status_code": status_code, "message": message})
