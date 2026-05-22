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
            self._ensure_column(conn, "spotify_tracks", "release_date", "TEXT")
            self._ensure_column(conn, "soundeo_matches", "soundeo_release_name", "TEXT")
            self._ensure_column(conn, "soundeo_matches", "soundeo_release_date", "TEXT")
            self._ensure_column(conn, "waitlist", "review_status", "TEXT NOT NULL DEFAULT 'active'")
            self._ensure_column(conn, "waitlist", "manual_review_reason", "TEXT")
            self._ensure_column(conn, "waitlist", "reviewed_at", "TEXT")

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

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
                    spotify_track_id, isrc, artists_raw, title_raw, release_date,
                    normalized_query, added_at, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(spotify_track_id) DO UPDATE SET
                    isrc = excluded.isrc,
                    artists_raw = excluded.artists_raw,
                    title_raw = excluded.title_raw,
                    release_date = excluded.release_date,
                    normalized_query = excluded.normalized_query,
                    added_at = excluded.added_at,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    track.spotify_track_id,
                    track.isrc,
                    track.artists_raw,
                    track.title_raw,
                    track.release_date,
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
                    spotify_track_id, soundeo_track_id, soundeo_url,
                    soundeo_release_name, soundeo_release_date, match_score, match_type,
                    availability_status, downloaded_flag, last_checked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(spotify_track_id, soundeo_track_id) DO UPDATE SET
                    soundeo_url = excluded.soundeo_url,
                    soundeo_release_name = excluded.soundeo_release_name,
                    soundeo_release_date = excluded.soundeo_release_date,
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
                    match.candidate.release_name if match.candidate else None,
                    match.candidate.release_date if match.candidate else None,
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

    def clear_waitlist(self, spotify_track_id: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM waitlist WHERE spotify_track_id = ?", (spotify_track_id,))

    def get_waitlist_tracks_due(self) -> list[str]:
        now = datetime.now(UTC).isoformat()
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT spotify_track_id
                FROM waitlist
                WHERE next_retry_at <= ?
                  AND review_status = 'active'
                ORDER BY next_retry_at ASC
                """,
                (now,),
            ).fetchall()
        return [row["spotify_track_id"] for row in rows]

    def waitlist_report(self, older_than_days: int | None = None, status: str | None = None) -> list[dict[str, object]]:
        params: list[object] = []
        filters: list[str] = []
        if older_than_days is not None:
            cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
            filters.append("s.added_at < ?")
            params.append(cutoff.isoformat())
        if status is not None:
            filters.append("w.review_status = ?")
            params.append(status)

        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        query = f"""
            SELECT
                w.spotify_track_id,
                s.artists_raw,
                s.title_raw,
                s.release_date,
                s.added_at,
                w.reason,
                w.retry_count,
                w.next_retry_at,
                w.last_retry_at,
                w.review_status,
                w.manual_review_reason,
                w.reviewed_at,
                (
                    SELECT sm.match_type || ':' || printf('%.1f', sm.match_score)
                    FROM soundeo_matches sm
                    WHERE sm.spotify_track_id = w.spotify_track_id
                    ORDER BY sm.last_checked_at DESC
                    LIMIT 1
                ) AS last_match,
                (
                    SELECT sm.soundeo_release_name
                    FROM soundeo_matches sm
                    WHERE sm.spotify_track_id = w.spotify_track_id
                    ORDER BY sm.last_checked_at DESC
                    LIMIT 1
                ) AS last_match_release_name,
                (
                    SELECT sm.soundeo_release_date
                    FROM soundeo_matches sm
                    WHERE sm.spotify_track_id = w.spotify_track_id
                    ORDER BY sm.last_checked_at DESC
                    LIMIT 1
                ) AS last_match_release_date
            FROM waitlist w
            JOIN spotify_tracks s ON s.spotify_track_id = w.spotify_track_id
            {where}
            ORDER BY s.added_at ASC, w.retry_count DESC
        """
        with self.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def mark_old_waitlist_for_manual_review(self, older_than_days: int, reason: str) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        now = datetime.now(UTC).isoformat()
        with self.connection() as conn:
            cursor = conn.execute(
                """
                UPDATE waitlist
                SET review_status = 'manual_review',
                    manual_review_reason = ?,
                    reviewed_at = ?
                WHERE review_status = 'active'
                  AND spotify_track_id IN (
                    SELECT spotify_track_id
                    FROM spotify_tracks
                    WHERE added_at < ?
                  )
                """,
                (reason, now, cutoff.isoformat()),
            )
            return cursor.rowcount

    def replace_downloads_cache(self, candidates: list[tuple[str, str, str | None]]) -> int:
        with self.connection() as conn:
            conn.execute("DELETE FROM downloads_cache")
            conn.executemany(
                """
                INSERT INTO downloads_cache(soundeo_track_id, normalized_track_key, downloaded_at, source)
                VALUES (?, ?, ?, 'parsed_from_downloaded_page')
                """,
                candidates,
            )
        return len(candidates)

    def upsert_downloads_cache(self, candidates: list[tuple[str, str, str | None]]) -> int:
        with self.connection() as conn:
            conn.executemany(
                """
                INSERT INTO downloads_cache(soundeo_track_id, normalized_track_key, downloaded_at, source)
                VALUES (?, ?, ?, 'parsed_from_downloaded_page_preflight')
                ON CONFLICT(soundeo_track_id) DO UPDATE SET
                    normalized_track_key = excluded.normalized_track_key,
                    downloaded_at = excluded.downloaded_at,
                    source = excluded.source
                """,
                candidates,
            )
        return len(candidates)

    def is_track_downloaded(self, normalized_track_key: str) -> bool:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM downloads_cache WHERE normalized_track_key = ? LIMIT 1",
                (normalized_track_key,),
            ).fetchone()
        return row is not None

    def is_soundeo_track_downloaded(self, soundeo_track_id: str) -> bool:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM downloads_cache WHERE soundeo_track_id = ? LIMIT 1",
                (soundeo_track_id,),
            ).fetchone()
        return row is not None

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
