from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator

from app.config import AppSettings
from app.models import ActionType, MatchResult, NormalizedTrack, RunSummary, SpotifyTrack, TrackStatus, WaitlistReason
from app.storage.schema import SCHEMA_SQL


def ensure_runtime_directories(settings: AppSettings) -> None:
    for path in (
        settings.data_dir,
        settings.logs_dir,
        settings.artifacts_dir,
        settings.screenshots_dir,
        settings.html_dir,
        settings.reports_dir,
        settings.playwright_state_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)


class SyncRepository:
    def __init__(self, sqlite_path: Path):
        self.sqlite_path = sqlite_path
        self._initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self.connection() as conn:
            conn.executescript(SCHEMA_SQL)

    def get_state(self, key: str) -> str | None:
        with self.connection() as conn:
            row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
        return None if row is None else row["value"]

    def set_state(self, key: str, value: str) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO sync_state(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def upsert_spotify_track(self, track: SpotifyTrack, normalized: NormalizedTrack) -> bool:
        now = datetime.now(UTC).isoformat()
        with self.connection() as conn:
            row = conn.execute(
                "SELECT spotify_track_id FROM spotify_tracks WHERE spotify_track_id = ?",
                (track.spotify_track_id,),
            ).fetchone()
            is_new = row is None
            conn.execute(
                """
                INSERT INTO spotify_tracks(
                    spotify_track_id, isrc, artists_raw, title_raw, normalized_query, added_at, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(spotify_track_id) DO UPDATE SET
                    isrc = excluded.isrc,
                    artists_raw = excluded.artists_raw,
                    title_raw = excluded.title_raw,
                    normalized_query = excluded.normalized_query,
                    added_at = excluded.added_at,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    track.spotify_track_id,
                    track.isrc,
                    track.artists_raw,
                    track.title_raw,
                    normalized.normalized_query,
                    track.added_at.isoformat(),
                    now,
                    now,
                ),
            )
        return is_new

    def record_match(self, spotify_track_id: str, match: MatchResult, status: TrackStatus) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO soundeo_matches(
                    spotify_track_id, soundeo_track_id, soundeo_url, match_score, match_type,
                    availability_status, downloaded_flag, last_checked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(spotify_track_id, soundeo_track_id) DO UPDATE SET
                    soundeo_url = excluded.soundeo_url,
                    match_score = excluded.match_score,
                    match_type = excluded.match_type,
                    availability_status = excluded.availability_status,
                    downloaded_flag = excluded.downloaded_flag,
                    last_checked_at = excluded.last_checked_at
                """,
                (
                    spotify_track_id,
                    match.candidate.soundeo_track_id if match.candidate else None,
                    match.candidate.url if match.candidate else None,
                    match.score,
                    match.match_type,
                    status.value,
                    int(bool(match.candidate and match.candidate.is_downloaded)),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def record_action(self, spotify_track_id: str, action_type: ActionType, result: str, notes: str = "") -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO actions(spotify_track_id, action_type, action_result, action_at, notes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    spotify_track_id,
                    action_type.value,
                    result,
                    datetime.now(UTC).isoformat(),
                    notes,
                ),
            )

    def put_waitlist(self, spotify_track_id: str, reason: WaitlistReason, retry_days: int) -> None:
        now = datetime.now(UTC)
        next_retry = now + timedelta(days=retry_days)
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO waitlist(spotify_track_id, reason, retry_count, next_retry_at, last_retry_at)
                VALUES (?, ?, 0, ?, ?)
                ON CONFLICT(spotify_track_id) DO UPDATE SET
                    reason = excluded.reason,
                    retry_count = waitlist.retry_count + 1,
                    next_retry_at = excluded.next_retry_at,
                    last_retry_at = excluded.last_retry_at
                """,
                (spotify_track_id, reason.value, next_retry.isoformat(), now.isoformat()),
            )

    def get_waitlist_tracks_due(self) -> list[str]:
        now = datetime.now(UTC).isoformat()
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT spotify_track_id FROM waitlist WHERE next_retry_at <= ? ORDER BY next_retry_at ASC",
                (now,),
            ).fetchall()
        return [row["spotify_track_id"] for row in rows]

    def was_action_recorded(self, spotify_track_id: str, action_type: ActionType) -> bool:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM actions WHERE spotify_track_id = ? AND action_type = ? LIMIT 1",
                (spotify_track_id, action_type.value),
            ).fetchone()
        return row is not None

    def export_summary(self, settings: AppSettings, summary: RunSummary) -> Path:
        filename = f"{summary.started_at.strftime('%Y%m%dT%H%M%SZ')}-{summary.mode}.json"
        output = settings.reports_dir / filename
        output.write_text(json.dumps(asdict(summary), default=str, indent=2), encoding="utf-8")
        return output

