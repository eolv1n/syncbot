from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from app.models import RunSummary

LOGGER = logging.getLogger(__name__)


def print_summary(summary: RunSummary, report_path: Path) -> None:
    payload = asdict(summary)
    LOGGER.info("Run summary: %s", json.dumps(payload, default=str))
    LOGGER.info("JSON report written to %s", report_path)

