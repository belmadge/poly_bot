from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request

from app.config import Settings

logger = logging.getLogger(__name__)


class AlertNotifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send(self, title: str, message: str) -> None:
        if not self._enabled():
            return
        threading.Thread(target=self._send_sync, args=(title, message), daemon=True).start()

    def _enabled(self) -> bool:
        return bool(
            self.settings.alert_discord_webhook_url
            or (self.settings.alert_telegram_bot_token and self.settings.alert_telegram_chat_id)
        )

    def _send_sync(self, title: str, message: str) -> None:
        full_message = f"{title}\n{message}"
        self._send_discord(full_message)
        self._send_telegram(full_message)

    def _send_discord(self, message: str) -> None:
        webhook_url = self.settings.alert_discord_webhook_url
        if not webhook_url:
            return
        data = json.dumps({"content": message}).encode("utf-8")
        request = urllib.request.Request(webhook_url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        self._send_request(request, "discord_alert_failed")

    def _send_telegram(self, message: str) -> None:
        token = self.settings.alert_telegram_bot_token
        chat_id = self.settings.alert_telegram_chat_id
        if not token or not chat_id:
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        encoded = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode("utf-8")
        request = urllib.request.Request(url, data=encoded, method="POST")
        self._send_request(request, "telegram_alert_failed")

    def _send_request(self, request: urllib.request.Request, event: str) -> None:
        try:
            with urllib.request.urlopen(request, timeout=5):
                return
        except (urllib.error.URLError, TimeoutError, ValueError):
            logger.exception("Alert delivery failed", extra={"event": event})
