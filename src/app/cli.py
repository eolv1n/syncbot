from __future__ import annotations

import argparse
from pathlib import Path

from app.config import AppSettings, load_settings
from app.logging_setup import configure_logging
from app.services.runner import SyncRunner
from app.storage.repository import ensure_runtime_directories


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("initial-sync", "daily-sync", "retry-waitlist", "dry-run"):
        subparsers.add_parser(command)

    env_parser = subparsers.add_parser("show-config")
    env_parser.add_argument("--as-paths", action="store_true")
    return parser


def _handle_show_config(settings: AppSettings, as_paths: bool) -> int:
    data = settings.model_dump()
    if as_paths:
        for key in (
            "base_dir",
            "data_dir",
            "logs_dir",
            "artifacts_dir",
            "screenshots_dir",
            "html_dir",
            "playwright_state_dir",
        ):
            data[key] = str(data[key])
    print(data)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = load_settings()
    ensure_runtime_directories(settings)
    configure_logging(settings)

    if args.command == "show-config":
        return _handle_show_config(settings, args.as_paths)

    runner = SyncRunner(settings=settings)
    return runner.run(args.command)

