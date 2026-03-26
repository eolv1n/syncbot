from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from app.config import AppSettings


def configure_logging(settings: AppSettings) -> None:
    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())
    root.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    log_file = settings.logs_dir / "app.log"
    file_handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=5)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

