from __future__ import annotations

import argparse

from app.config import AppSettings, load_settings
from app.integrations.spotify import SpotifyAuthFlow
from app.logging_setup import configure_logging
from app.services.runner import SyncRunner
from app.storage.repository import ensure_runtime_directories


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("initial-sync", "full-sync", "daily-sync", "retry-waitlist", "dry-run", "sync-downloads-cache"):
        subparsers.add_parser(command)

    auth_parser = subparsers.add_parser("spotify-auth")
    auth_parser.add_argument("--no-browser", action="store_true")
    auth_parser.add_argument("--timeout", type=int, default=180, help="Callback wait timeout in seconds")

    auth_url_parser = subparsers.add_parser("spotify-auth-url")
    auth_url_parser.add_argument("--state", default="", help="Optional fixed state for debugging")

    exchange_parser = subparsers.add_parser("spotify-auth-exchange")
    exchange_parser.add_argument("--code", required=True)

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
    settings.dry_run = args.command == "dry-run"
    ensure_runtime_directories(settings)
    configure_logging(settings)

    if args.command == "show-config":
        return _handle_show_config(settings, args.as_paths)
    if args.command == "spotify-auth":
        flow = SpotifyAuthFlow(settings)
        return flow.run_interactive(no_browser=args.no_browser, timeout_seconds=args.timeout)
    if args.command == "spotify-auth-url":
        flow = SpotifyAuthFlow(settings)
        print(flow.build_authorize_url(flow.make_state(args.state)))
        return 0
    if args.command == "spotify-auth-exchange":
        flow = SpotifyAuthFlow(settings)
        return flow.exchange_code(args.code)

    runner = SyncRunner(settings=settings)
    return runner.run(args.command)
