"""入口：python -m console"""

from __future__ import annotations

import logging

import uvicorn

from .app import create_app
from .config import load_console_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def main() -> None:
    cfg = load_console_settings()
    app = create_app(cfg)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
