from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from app.config import AppSettings, load_settings
from app.integrations.spotify import SpotifyAuthFlow
from app.logging_setup import configure_logging
from app.services.runner import SyncRunner
from app.storage.repository import ensure_runtime_directories


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in (
        "initial-sync",
        "full-sync",
        "fresh-sync",
        "daily-sync",
        "retry-waitlist",
        "dry-run",
        "sync-downloads-cache",
        "refresh-spotify-metadata",
    ):
        subparsers.add_parser(command)

    sync_track_ids_parser = subparsers.add_parser("sync-track-ids")
    sync_track_ids_parser.add_argument("spotify_track_ids", nargs="+")

    report_parser = subparsers.add_parser("waitlist-report")
    report_parser.add_argument("--older-than-days", type=int, default=None)
    report_parser.add_argument("--status", default=None, choices=("active", "manual_review"))
    report_parser.add_argument("--output", default="")

    manual_review_parser = subparsers.add_parser("mark-old-waitlist-manual-review")
    manual_review_parser.add_argument("--older-than-days", type=int, default=365)
    manual_review_parser.add_argument("--reason", default="")
    manual_review_parser.add_argument("--apply", action="store_true")

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


def _write_waitlist_report(settings: AppSettings, rows: list[dict[str, object]], output: str) -> None:
    if output:
        path = settings.base_dir / output if not output.startswith("/") else Path(output)
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = settings.reports_dir / f"{stamp}-waitlist-report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Waitlist report written to {path}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = load_settings()
    settings.dry_run = args.command == "dry-run"
    ensure_runtime_directories(settings)
    configure_logging(settings)

    if args.command == "show-config":
        return _handle_show_config(settings, args.as_paths)
    if args.command == "waitlist-report":
        runner = SyncRunner(settings=settings)
        try:
            rows = runner.repository.waitlist_report(
                older_than_days=args.older_than_days,
                status=args.status,
            )
            _write_waitlist_report(settings, rows, args.output)
            print(f"Rows: {len(rows)}")
            return 0
        finally:
            runner.soundeo.close()
    if args.command == "mark-old-waitlist-manual-review":
        runner = SyncRunner(settings=settings)
        try:
            reason = args.reason or f"older_than_{args.older_than_days}_days"
            rows = runner.repository.waitlist_report(
                older_than_days=args.older_than_days,
                status="active",
            )
            if not args.apply:
                print(f"Would mark {len(rows)} active waitlist tracks for manual review. Use --apply to change SQLite.")
                return 0
            changed = runner.repository.mark_old_waitlist_for_manual_review(args.older_than_days, reason)
            print(f"Marked {changed} waitlist tracks for manual review.")
            return 0
        finally:
            runner.soundeo.close()
    if args.command == "sync-track-ids":
        runner = SyncRunner(settings=settings)
        return runner.run_track_ids(set(args.spotify_track_ids))
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
