from __future__ import annotations

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.controller import BotController
from app.log_buffer import get_recent_logs

logger = logging.getLogger(__name__)

_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PolyBot Dashboard</title>
  <style>
    :root {
      --bg: #f4efe7;
      --panel: rgba(255, 250, 244, 0.92);
      --ink: #1e1f1b;
      --muted: #6a665d;
      --accent: #0f766e;
      --accent-2: #b45309;
      --danger: #b91c1c;
      --line: rgba(30, 31, 27, 0.1);
      --shadow: 0 18px 40px rgba(47, 39, 30, 0.14);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(180, 83, 9, 0.12), transparent 28%),
        radial-gradient(circle at top right, rgba(15, 118, 110, 0.16), transparent 24%),
        linear-gradient(180deg, #f8f3eb 0%, var(--bg) 100%);
      min-height: 100vh;
    }
    .shell {
      max-width: 1200px;
      margin: 0 auto;
      padding: 32px 20px 40px;
    }
    .hero {
      display: grid;
      grid-template-columns: 1.6fr 1fr;
      gap: 18px;
      align-items: stretch;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
    }
    .hero-copy {
      padding: 28px;
    }
    h1 {
      margin: 0 0 12px;
      font-size: clamp(2.2rem, 4vw, 4.3rem);
      line-height: 0.92;
      letter-spacing: -0.04em;
    }
    .subtitle {
      margin: 0;
      color: var(--muted);
      font-size: 1rem;
      line-height: 1.6;
      max-width: 58ch;
    }
    .hero-side {
      padding: 24px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      gap: 16px;
      background:
        linear-gradient(140deg, rgba(15, 118, 110, 0.9), rgba(8, 47, 73, 0.9)),
        linear-gradient(180deg, rgba(255,255,255,0.05), rgba(255,255,255,0));
      color: #f9fafb;
    }
    .status-line {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      flex-wrap: wrap;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 10px 14px;
      border-radius: 999px;
      font-size: 0.92rem;
      background: rgba(255,255,255,0.12);
      border: 1px solid rgba(255,255,255,0.16);
    }
    .actions {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
    }
    button {
      border: 0;
      border-radius: 999px;
      padding: 12px 18px;
      font: inherit;
      cursor: pointer;
      transition: transform 120ms ease, opacity 120ms ease, background 120ms ease;
    }
    button:hover { transform: translateY(-1px); }
    button:disabled { opacity: 0.5; cursor: wait; transform: none; }
    .primary { background: #f8fafc; color: #062c2c; }
    .ghost { background: rgba(255,255,255,0.12); color: #f8fafc; border: 1px solid rgba(255,255,255,0.16); }
    .grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 16px;
      margin-top: 18px;
    }
    .card {
      padding: 20px;
      min-height: 136px;
      position: relative;
      overflow: hidden;
    }
    .card::after {
      content: "";
      position: absolute;
      inset: auto -40px -40px auto;
      width: 120px;
      height: 120px;
      border-radius: 999px;
      background: rgba(15, 118, 110, 0.07);
    }
    .eyebrow {
      margin: 0 0 12px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.12em;
      font-size: 0.72rem;
    }
    .metric {
      margin: 0;
      font-size: clamp(1.9rem, 2.6vw, 2.8rem);
      line-height: 1;
      letter-spacing: -0.04em;
    }
    .metric-note {
      margin-top: 10px;
      color: var(--muted);
      font-size: 0.95rem;
    }
    .meta {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
      margin-top: 18px;
    }
    .meta-box, .log-box {
      padding: 22px;
    }
    .meta-list {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px 18px;
      margin-top: 10px;
    }
    .meta-item span {
      display: block;
    }
    .meta-label {
      color: var(--muted);
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 5px;
    }
    .meta-value {
      font-size: 1.06rem;
      word-break: break-word;
    }
    .error {
      margin-top: 14px;
      padding: 12px 14px;
      border-radius: 16px;
      background: rgba(185, 28, 28, 0.08);
      border: 1px solid rgba(185, 28, 28, 0.18);
      color: var(--danger);
      display: none;
    }
    .logs {
      max-height: 420px;
      overflow: auto;
      margin-top: 14px;
      border-top: 1px solid var(--line);
      padding-top: 12px;
    }
    .log-row {
      padding: 12px 0;
      border-bottom: 1px solid rgba(30, 31, 27, 0.08);
    }
    .log-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
      font-size: 0.86rem;
      color: var(--muted);
    }
    .log-message {
      margin-top: 6px;
      font-size: 0.98rem;
      line-height: 1.5;
    }
    .pill {
      display: inline-block;
      padding: 4px 8px;
      border-radius: 999px;
      background: rgba(15, 118, 110, 0.08);
      color: var(--accent);
      font-size: 0.82rem;
    }
    @media (max-width: 980px) {
      .hero, .meta { grid-template-columns: 1fr; }
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .meta-list { grid-template-columns: 1fr; }
    }
    @media (max-width: 640px) {
      .shell { padding: 20px 14px 30px; }
      .grid { grid-template-columns: 1fr; }
      h1 { font-size: 2.3rem; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <article class="panel hero-copy">
        <p class="eyebrow">Polymarket Desk</p>
        <h1>PolyBot Web Command</h1>
        <p class="subtitle">Painel local para acompanhar operacao, inventario, pausas de protecao e ultimos eventos do market maker em tempo real.</p>
      </article>
      <aside class="panel hero-side">
        <div class="status-line">
          <div id="statusBadge" class="badge">Loading status...</div>
          <div class="actions">
            <button id="startBtn" class="primary">Start Bot</button>
            <button id="stopBtn" class="ghost">Stop Bot</button>
          </div>
        </div>
        <div>
          <p class="eyebrow" style="color: rgba(248,250,252,0.72);">Control</p>
          <div id="controlMessage">Use o painel para iniciar, parar e observar o estado atual do bot.</div>
        </div>
      </aside>
    </section>

    <section class="grid">
      <article class="panel card">
        <p class="eyebrow">Gross Bought</p>
        <p id="grossBought" class="metric">-</p>
        <div class="metric-note">Quanto entrou na compra</div>
      </article>
      <article class="panel card">
        <p class="eyebrow">Gross Sold</p>
        <p id="grossSold" class="metric">-</p>
        <div class="metric-note">Quanto saiu na venda</div>
      </article>
      <article class="panel card">
        <p class="eyebrow">Current Position</p>
        <p id="positionSize" class="metric">-</p>
        <div id="netPositionNote" class="metric-note">Posicao liquida atual</div>
      </article>
      <article class="panel card">
        <p class="eyebrow">Realized PnL</p>
        <p id="realizedPnl" class="metric">-</p>
        <div class="metric-note">Resultado realizado</div>
      </article>
    </section>

    <section class="meta">
      <article class="panel meta-box">
        <p class="eyebrow">Runtime</p>
        <div class="meta-list">
          <div class="meta-item"><span class="meta-label">Token</span><span id="tokenId" class="meta-value">-</span></div>
          <div class="meta-item"><span class="meta-label">Dry Run</span><span id="dryRun" class="meta-value">-</span></div>
          <div class="meta-item"><span class="meta-label">Open Orders</span><span id="openOrders" class="meta-value">-</span></div>
          <div class="meta-item"><span class="meta-label">Trades Executed</span><span id="tradesExecuted" class="meta-value">-</span></div>
          <div class="meta-item"><span class="meta-label">Cycles Without Fill</span><span id="cyclesWithoutFill" class="meta-value">-</span></div>
          <div class="meta-item"><span class="meta-label">API Errors</span><span id="apiErrors" class="meta-value">-</span></div>
          <div class="meta-item"><span class="meta-label">Last Price</span><span id="lastMarketPrice" class="meta-value">-</span></div>
          <div class="meta-item"><span class="meta-label">Paused Until</span><span id="pausedUntil" class="meta-value">-</span></div>
        </div>
        <div id="errorBox" class="error"></div>
      </article>

      <article class="panel log-box">
        <p class="eyebrow">Recent Events</p>
        <div id="logs" class="logs"></div>
      </article>
    </section>
  </main>

  <script>
    const statusBadge = document.getElementById("statusBadge");
    const controlMessage = document.getElementById("controlMessage");
    const errorBox = document.getElementById("errorBox");
    const startBtn = document.getElementById("startBtn");
    const stopBtn = document.getElementById("stopBtn");

    function fmt(value, digits = 4) {
      if (value === null || value === undefined || value === "") return "-";
      if (typeof value === "number") return value.toFixed(digits);
      return String(value);
    }

    function fmtTime(value) {
      if (!value || value <= 0) return "-";
      return new Date(value * 1000).toLocaleString();
    }

    function setMetric(id, value, digits = 4) {
      document.getElementById(id).textContent = fmt(value, digits);
    }

    async function callAction(path, button) {
      button.disabled = true;
      try {
        const response = await fetch(path, { method: "POST" });
        const payload = await response.json();
        controlMessage.textContent = payload.message || "Action completed";
        await refresh();
      } catch (error) {
        controlMessage.textContent = "Action failed: " + error.message;
      } finally {
        button.disabled = false;
      }
    }

    async function refreshStatus() {
      const response = await fetch("/api/status");
      const payload = await response.json();
      const metrics = payload.metrics || {};

      statusBadge.textContent = payload.running ? "Bot running" : "Bot stopped";
      statusBadge.style.background = payload.running ? "rgba(16, 185, 129, 0.16)" : "rgba(248, 250, 252, 0.12)";

      document.getElementById("tokenId").textContent = payload.token_id || "-";
      document.getElementById("dryRun").textContent = payload.dry_run === null ? "-" : (payload.dry_run ? "true" : "false");
      document.getElementById("openOrders").textContent = metrics.open_orders ?? "-";
      document.getElementById("tradesExecuted").textContent = metrics.trades_executed ?? "-";
      document.getElementById("cyclesWithoutFill").textContent = metrics.cycles_without_fill ?? "-";
      document.getElementById("apiErrors").textContent = metrics.consecutive_api_errors ?? "-";
      document.getElementById("lastMarketPrice").textContent = fmt(metrics.last_market_price, 4);
      document.getElementById("pausedUntil").textContent = fmtTime(metrics.paused_until);

      setMetric("grossBought", metrics.gross_bought);
      setMetric("grossSold", metrics.gross_sold);
      setMetric("positionSize", metrics.position_size);
      setMetric("realizedPnl", metrics.realized_pnl);
      document.getElementById("netPositionNote").textContent = "Net position: " + fmt(metrics.net_position);

      if (payload.last_error) {
        errorBox.style.display = "block";
        errorBox.textContent = payload.last_error;
      } else {
        errorBox.style.display = "none";
        errorBox.textContent = "";
      }
    }

    async function refreshLogs() {
      const response = await fetch("/api/logs?limit=80");
      const payload = await response.json();
      const container = document.getElementById("logs");
      container.innerHTML = "";
      for (const item of payload.logs || []) {
        const row = document.createElement("div");
        row.className = "log-row";
        row.innerHTML = `
          <div class="log-head">
            <span>${item.timestamp || ""}</span>
            <span><span class="pill">${item.level || "INFO"}</span> ${item.event || ""}</span>
          </div>
          <div class="log-message">${item.message || ""}</div>
        `;
        container.appendChild(row);
      }
    }

    async function refresh() {
      await Promise.all([refreshStatus(), refreshLogs()]);
    }

    startBtn.addEventListener("click", () => callAction("/api/start", startBtn));
    stopBtn.addEventListener("click", () => callAction("/api/stop", stopBtn));

    refresh();
    setInterval(refresh, 2000);
  </script>
</body>
</html>
"""


def create_server(controller: BotController, host: str, port: int) -> ThreadingHTTPServer:
    handler_class = _build_handler(controller)
    return ThreadingHTTPServer((host, port), handler_class)


def serve_dashboard(controller: BotController, host: str = "127.0.0.1", port: int = 8000) -> None:
    server = create_server(controller=controller, host=host, port=port)
    logger.info("Dashboard listening", extra={"event": "web_server_start", "host": host, "port": port})
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Dashboard interrupted", extra={"event": "web_server_stop"})
    finally:
        controller.stop()
        server.server_close()


def _build_handler(controller: BotController) -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._respond_html(_DASHBOARD_HTML)
                return
            if parsed.path == "/api/status":
                self._respond_json(controller.status())
                return
            if parsed.path == "/api/logs":
                limit = int(parse_qs(parsed.query).get("limit", ["80"])[0])
                self._respond_json({"logs": get_recent_logs(limit)})
                return
            if parsed.path == "/health":
                self._respond_json({"ok": True})
                return
            self._respond_json({"ok": False, "message": "Not found"}, status=HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/start":
                self._respond_json(controller.start())
                return
            if parsed.path == "/api/stop":
                self._respond_json(controller.stop())
                return
            self._respond_json({"ok": False, "message": "Not found"}, status=HTTPStatus.NOT_FOUND)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _respond_html(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            payload = body.encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _respond_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return DashboardHandler
