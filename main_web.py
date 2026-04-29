from __future__ import annotations

import logging
import os

from app.config import ConfigError, load_settings
from app.controller import BotController
from app.logging_config import setup_logging
from app.web import serve_dashboard


def main() -> None:
    try:
        settings = load_settings()
        setup_logging(settings)
    except ConfigError:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    host = os.getenv("WEB_HOST", "127.0.0.1")
    port = int(os.getenv("WEB_PORT", "8000"))
    controller = BotController()
    serve_dashboard(controller=controller, host=host, port=port)


if __name__ == "__main__":
    main()
