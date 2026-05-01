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
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PolyBot Web Command - Painel de Controle</title>
  <style>
    :root {
      --primary: #10b981;
      --primary-dark: #059669;
      --danger: #ef4444;
      --warning: #f59e0b;
      --bg: #0f172a;
      --bg-secondary: #1e293b;
      --bg-tertiary: #334155;
      --text: #f1f5f9;
      --text-muted: #cbd5e1;
      --border: #475569;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      color: var(--text);
      background: linear-gradient(135deg, var(--bg) 0%, #1a1f35 100%);
      min-height: 100vh;
      padding: 20px;
    }
    .shell {
      max-width: 1200px;
      margin: 0 auto;
    }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 40px;
      flex-wrap: wrap;
      gap: 20px;
    }
    .header-title h1 {
      margin: 0;
      font-size: 2.5rem;
      font-weight: 700;
    }
    .header-subtitle {
      color: var(--text-muted);
      margin-top: 4px;
    }
    .status-control {
      display: flex;
      gap: 12px;
      align-items: center;
      flex-wrap: wrap;
    }
    .status-badge {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 10px 16px;
      border-radius: 999px;
      background: rgba(16, 185, 129, 0.15);
      color: var(--primary);
      border: 1px solid rgba(16, 185, 129, 0.3);
      font-weight: 600;
    }
    .status-badge.stopped {
      background: rgba(107, 114, 128, 0.15);
      color: var(--text-muted);
      border-color: rgba(107, 114, 128, 0.3);
    }
    .metrics-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }
    .metric-card {
      background: var(--bg-secondary);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 20px;
      transition: all 0.3s ease;
    }
    .metric-card:hover {
      border-color: var(--primary);
      box-shadow: 0 0 20px rgba(16, 185, 129, 0.1);
    }
    .metric-label {
      color: var(--text-muted);
      font-size: 0.85rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 8px;
      font-weight: 600;
    }
    .metric-value {
      font-size: 2rem;
      font-weight: 700;
      color: var(--primary);
      margin: 0;
    }
    .metric-unit {
      color: var(--text-muted);
      font-size: 0.9rem;
      margin-top: 4px;
    }
    .section {
      background: var(--bg-secondary);
      border: 1px solid var(--border);
      border-radius: 12px;
      margin-bottom: 24px;
      overflow: hidden;
    }
    .section-header {
      padding: 16px 20px;
      border-bottom: 1px solid var(--border);
      background: rgba(0, 0, 0, 0.2);
    }
    .section-header h2 {
      margin: 0;
      font-size: 1.1rem;
      font-weight: 600;
    }
    .section-body {
      padding: 20px;
    }
    .info-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 16px;
    }
    .info-label {
      color: var(--text-muted);
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 4px;
      font-weight: 600;
    }
    .info-value {
      color: var(--text);
      font-size: 1rem;
      font-weight: 500;
      word-break: break-word;
    }
    .logs-container {
      max-height: 400px;
      overflow-y: auto;
    }
    .log-item {
      padding: 12px;
      border-bottom: 1px solid var(--border);
      display: flex;
      gap: 12px;
      font-size: 0.9rem;
    }
    .log-time {
      color: var(--text-muted);
      white-space: nowrap;
      font-size: 0.8rem;
      min-width: 160px;
    }
    .log-badge {
      display: inline-block;
      padding: 3px 8px;
      border-radius: 4px;
      font-size: 0.75rem;
      font-weight: 600;
      white-space: nowrap;
      background: rgba(16, 185, 129, 0.2);
      color: var(--primary);
    }
    .log-badge.error {
      background: rgba(239, 68, 68, 0.2);
      color: var(--danger);
    }
    .log-badge.warning {
      background: rgba(245, 158, 11, 0.2);
      color: var(--warning);
    }
    .log-message {
      flex: 1;
      color: var(--text-muted);
    }
    .buttons {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
    }
    button {
      padding: 12px 24px;
      border: none;
      border-radius: 8px;
      font: inherit;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s ease;
    }
    .btn-primary {
      background: var(--primary);
      color: white;
    }
    .btn-primary:hover:not(:disabled) {
      background: var(--primary-dark);
      transform: translateY(-2px);
      box-shadow: 0 10px 20px rgba(16, 185, 129, 0.2);
    }
    .btn-secondary {
      background: var(--bg-tertiary);
      color: var(--text);
      border: 1px solid var(--border);
    }
    .btn-secondary:hover:not(:disabled) {
      background: var(--border);
      transform: translateY(-2px);
    }
    button:disabled {
      opacity: 0.5;
      cursor: wait;
    }
    .error-box {
      background: rgba(239, 68, 68, 0.15);
      border: 1px solid rgba(239, 68, 68, 0.3);
      color: var(--danger);
      padding: 12px 16px;
      border-radius: 8px;
      margin-top: 12px;
      display: none;
    }
    .error-box.show {
      display: block;
    }
    .spinner {
      display: inline-block;
      width: 14px;
      height: 14px;
      border: 2px solid rgba(16, 185, 129, 0.3);
      border-top-color: var(--primary);
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }
    @keyframes spin {
      to { transform: rotate(360deg); }
    }
    @media (max-width: 768px) {
      .header { flex-direction: column; align-items: flex-start; }
      .metrics-grid { grid-template-columns: repeat(2, 1fr); }
      .info-grid { grid-template-columns: 1fr; }
      .header-title h1 { font-size: 1.8rem; }
    }
    @media (max-width: 480px) {
      .metrics-grid { grid-template-columns: 1fr; }
      button { width: 100%; }
      .buttons { flex-direction: column; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <div class="header">
      <div class="header-title">
        <h1>🤖 PolyBot</h1>
        <div class="header-subtitle">Painel de Controle do Market Maker</div>
      </div>
      <div class="status-control">
        <div id="statusBadge" class="status-badge">
          <span class="spinner"></span>
          Carregando...
        </div>
        <div class="buttons">
          <button id="startBtn" class="btn-primary">▶ Iniciar Bot</button>
          <button id="stopBtn" class="btn-secondary">⏹ Parar Bot</button>
        </div>
      </div>
    </div>

    <div class="metrics-grid">
      <div class="metric-card">
        <div class="metric-label">📊 Volume Comprado</div>
        <p class="metric-value" id="grossBought">-</p>
        <div class="metric-unit">Total em compras</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">📊 Volume Vendido</div>
        <p class="metric-value" id="grossSold">-</p>
        <div class="metric-unit">Total em vendas</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">💰 Posição Atual</div>
        <p class="metric-value" id="positionSize">-</p>
        <div class="metric-unit" id="netPositionNote">Posição líquida</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">📈 Lucro Realizado</div>
        <p class="metric-value" id="realizedPnl">-</p>
        <div class="metric-unit">PnL</div>
      </div>
    </div>

    <div class="section">
      <div class="section-header">
        <h2>⚙️ Informações do Sistema</h2>
      </div>
      <div class="section-body">
        <div class="info-grid">
          <div>
            <div class="info-label">Mercado (Token ID)</div>
            <div class="info-value" id="tokenId">-</div>
          </div>
          <div>
            <div class="info-label">Modo</div>
            <div class="info-value" id="dryRun">-</div>
          </div>
          <div>
            <div class="info-label">Ordens Abertas</div>
            <div class="info-value" id="openOrders">-</div>
          </div>
          <div>
            <div class="info-label">Trades Executados</div>
            <div class="info-value" id="tradesExecuted">-</div>
          </div>
          <div>
            <div class="info-label">Ciclos sem Preenchimento</div>
            <div class="info-value" id="cyclesWithoutFill">-</div>
          </div>
          <div>
            <div class="info-label">Erros de API</div>
            <div class="info-value" id="apiErrors">-</div>
          </div>
          <div>
            <div class="info-label">Último Preço</div>
            <div class="info-value" id="lastMarketPrice">-</div>
          </div>
          <div>
            <div class="info-label">Pausa Até</div>
            <div class="info-value" id="pausedUntil">-</div>
          </div>
        </div>
        <div id="errorBox" class="error-box"></div>
      </div>
    </div>

    <div class="section">
      <div class="section-header">
        <h2>📋 Eventos Recentes</h2>
      </div>
      <div class="section-body">
        <div id="logs" class="logs-container"></div>
      </div>
    </div>
  </main>

  <script>
    const statusBadge = document.getElementById("statusBadge");
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
      const d = new Date(value * 1000);
      return d.toLocaleString("pt-BR");
    }

    function setMetric(id, value, digits = 4) {
      document.getElementById(id).textContent = fmt(value, digits);
    }

    function getLevelColor(level) {
      if (!level) return "INFO";
      const l = level.toUpperCase();
      if (l.includes("ERROR") || l.includes("ERRO")) return "error";
      if (l.includes("WARNING") || l.includes("AVISO")) return "warning";
      return "INFO";
    }

    async function callAction(path, button) {
      button.disabled = true;
      try {
        const response = await fetch(path, { method: "POST" });
        const payload = await response.json();
        setTimeout(() => refresh(), 500);
      } catch (error) {
        console.error(error);
      } finally {
        button.disabled = false;
      }
    }

    async function refreshStatus() {
      try {
        const response = await fetch("/api/status");
        const payload = await response.json();
        const metrics = payload.metrics || {};

        const running = payload.running;
        statusBadge.textContent = running ? "✓ Bot Ativo" : "○ Bot Parado";
        statusBadge.className = running ? "status-badge" : "status-badge stopped";

        startBtn.disabled = running;
        stopBtn.disabled = !running;

        document.getElementById("tokenId").textContent = payload.token_id || "-";
        const dryRunText = payload.dry_run === null ? "-" : (payload.dry_run ? "🔒 Simulação (DRY_RUN)" : "🔴 Produção");
        document.getElementById("dryRun").textContent = dryRunText;
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
        document.getElementById("netPositionNote").textContent = "Posição: " + fmt(metrics.net_position);

        if (payload.last_error) {
          errorBox.classList.add("show");
          errorBox.textContent = "⚠️ " + payload.last_error;
        } else {
          errorBox.classList.remove("show");
        }
      } catch (error) {
        console.error("Status update failed:", error);
      }
    }

    async function refreshLogs() {
      try {
        const response = await fetch("/api/logs?limit=50");
        const payload = await response.json();
        const container = document.getElementById("logs");
        container.innerHTML = "";
        
        for (const item of payload.logs || []) {
          const row = document.createElement("div");
          row.className = "log-item";
          const levelColor = getLevelColor(item.level);
          row.innerHTML = `
            <div class="log-time">${item.timestamp || "-"}</div>
            <div class="log-badge ${levelColor}">${item.level || "INFO"}</div>
            <div class="log-message">${item.message || item.event || "-"}</div>
          `;
          container.appendChild(row);
        }
      } catch (error) {
        console.error("Logs update failed:", error);
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
