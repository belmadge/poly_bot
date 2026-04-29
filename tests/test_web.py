from __future__ import annotations

import json
import threading
from http.client import HTTPConnection

from app.web import create_server


class FakeController:
    def __init__(self) -> None:
        self.running = False

    def status(self) -> dict[str, object]:
        return {
            "running": self.running,
            "token_id": "demo-token",
            "dry_run": True,
            "last_error": None,
            "config_loaded": True,
            "metrics": {
                "gross_bought": 10.0,
                "gross_sold": 12.0,
                "position_size": 1.5,
                "net_position": 1.5,
                "realized_pnl": 2.0,
                "open_orders": 2,
                "trades_executed": 3,
                "cycles_without_fill": 0,
                "consecutive_api_errors": 0,
                "last_market_price": 0.5,
                "paused_until": 0.0,
            },
            "state": {"known_orders": {}},
        }

    def start(self) -> dict[str, object]:
        self.running = True
        return {"ok": True, "message": "Bot started"}

    def stop(self) -> dict[str, object]:
        self.running = False
        return {"ok": True, "message": "Stop requested"}


def test_dashboard_http_endpoints():
    controller = FakeController()
    server = create_server(controller=controller, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)

        conn.request("GET", "/")
        response = conn.getresponse()
        html = response.read().decode("utf-8")
        assert response.status == 200
        assert "PolyBot Web Command" in html

        conn.request("GET", "/api/status")
        response = conn.getresponse()
        status_payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert status_payload["token_id"] == "demo-token"
        assert status_payload["running"] is False

        conn.request("POST", "/api/start")
        response = conn.getresponse()
        start_payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert start_payload["ok"] is True
        assert controller.running is True

        conn.request("POST", "/api/stop")
        response = conn.getresponse()
        stop_payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert stop_payload["ok"] is True
        assert controller.running is False

        conn.request("GET", "/api/logs?limit=5")
        response = conn.getresponse()
        logs_payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert "logs" in logs_payload
        assert isinstance(logs_payload["logs"], list)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
