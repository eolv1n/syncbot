from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from app.config import AppSettings
from app.integrations.soundeo import SoundeoAutomation
from app.integrations.spotify import SpotifyClient
from app.matching.matcher import pick_best_match
from app.matching.normalizer import normalize_track
from app.models import ActionType, RunSummary, SpotifyTrack, TrackStatus, WaitlistReason
from app.services.reporting import print_summary
from app.storage.repository import SyncRepository

LOGGER = logging.getLogger(__name__)


class SyncRunner:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.repository = SyncRepository(settings.sqlite_path)
        self.spotify = SpotifyClient(settings)
        self.soundeo = SoundeoAutomation(settings)

    def run(self, mode: str) -> int:
        summary = RunSummary(mode=mode)
        try:
            if mode == "sync-downloads-cache":
                self._sync_downloads_cache(summary)
            elif mode in {"initial-sync", "full-sync"}:
                self._run_tracks(self.spotify.get_liked_tracks(), summary)
                self.repository.set_state("last_full_sync_at", summary.started_at.isoformat())
            elif mode == "daily-sync":
                after = self._daily_after()
                retried_track_ids = self._retry_waitlist(summary)
                fresh_tracks = [
                    track
                    for track in self.spotify.get_liked_tracks(after=after)
                    if track.spotify_track_id not in retried_track_ids
                ]
                self._run_tracks(fresh_tracks, summary)
                self.repository.set_state("last_daily_sync_at", datetime.now(UTC).isoformat())
            elif mode == "retry-waitlist":
                self._retry_waitlist(summary)
            elif mode == "dry-run":
                previous = self.settings.dry_run
                self.settings.dry_run = True
                self._run_tracks(self.spotify.get_liked_tracks(after=self._daily_after()), summary)
                self.settings.dry_run = previous
            else:
                raise ValueError(f"Unsupported mode: {mode}")
        except Exception as exc:
            LOGGER.exception("Sync failed: %s", exc)
            summary.errors += 1
            summary.problem_tracks.append(str(exc))
            report_path = self.repository.export_summary(self.settings, summary)
            print_summary(summary, report_path)
            return 1
        finally:
            self.soundeo.close()

        report_path = self.repository.export_summary(self.settings, summary)
        print_summary(summary, report_path)
        return 0

    def _last_daily_sync(self) -> datetime | None:
        value = self.repository.get_state("last_daily_sync_at")
        return datetime.fromisoformat(value) if value else None

    def _daily_after(self) -> datetime:
        last_sync = self._last_daily_sync()
        if last_sync is not None:
            return last_sync
        return datetime.now(UTC) - timedelta(days=self.settings.spotify_recent_days_on_first_sync)

    def _run_tracks(self, tracks: list[SpotifyTrack], summary: RunSummary) -> None:
        for track in tracks:
            summary.processed += 1
            normalized = normalize_track(track)
            if self.repository.upsert_spotify_track(track, normalized):
                summary.newly_seen += 1
            try:
                self._process_track(track, normalized, summary)
            except Exception as exc:
                LOGGER.exception("Track sync failed: %s", self._problem_track_label(track, str(exc)))
                summary.errors += 1
                summary.problem_tracks.append(self._problem_track_label(track, str(exc)))

    def _process_track(self, track: SpotifyTrack, normalized, summary: RunSummary) -> None:
        if self.repository.is_track_downloaded(normalized.normalized_query):
            if not self.settings.dry_run:
                self.repository.record_action(track.spotify_track_id, ActionType.SKIP, TrackStatus.DOWNLOADED_ALREADY.value)
            summary.downloaded_already += 1
            return

        candidates = self.soundeo.search_track(normalized)
        match = pick_best_match(track, candidates)

        if match.candidate is None:
            if not self.settings.dry_run:
                self.repository.record_match(track.spotify_track_id, match, TrackStatus.NOT_FOUND_WAITLIST)
                self.repository.put_waitlist(track.spotify_track_id, WaitlistReason.NOT_FOUND, self.settings.waitlist_retry_days)
                self.repository.record_action(track.spotify_track_id, ActionType.WAITLIST_ADD, TrackStatus.NOT_FOUND_WAITLIST.value)
            summary.waitlisted += 1
            return

        if self.repository.is_soundeo_track_downloaded(match.candidate.soundeo_track_id):
            if not self.settings.dry_run:
                self.repository.record_match(track.spotify_track_id, match, TrackStatus.DOWNLOADED_ALREADY)
                self.repository.record_action(track.spotify_track_id, ActionType.SKIP, TrackStatus.DOWNLOADED_ALREADY.value)
            summary.downloaded_already += 1
            return

        if match.candidate.is_downloaded:
            if not self.settings.dry_run:
                self.repository.record_match(track.spotify_track_id, match, TrackStatus.DOWNLOADED_ALREADY)
                self.repository.record_action(track.spotify_track_id, ActionType.SKIP, TrackStatus.DOWNLOADED_ALREADY.value)
            summary.downloaded_already += 1
            return

        if match.candidate.is_available:
            if not self.repository.was_action_recorded(track.spotify_track_id, ActionType.STAR):
                status = self.soundeo.apply_action(match, ActionType.STAR)
                if status == TrackStatus.STARRED:
                    if not self.settings.dry_run:
                        self.repository.record_action(track.spotify_track_id, ActionType.STAR, status.value)
                        self.repository.record_match(track.spotify_track_id, match, status)
                    summary.starred += 1
                elif status == TrackStatus.ERROR:
                    summary.errors += 1
                    summary.problem_tracks.append(self._problem_track_label(track))
            return

        if not self.repository.was_action_recorded(track.spotify_track_id, ActionType.LIKE):
            status = self.soundeo.apply_action(match, ActionType.LIKE)
            if status == TrackStatus.LIKED_WAITING_AVAILABILITY:
                if not self.settings.dry_run:
                    self.repository.record_action(track.spotify_track_id, ActionType.LIKE, status.value)
                    self.repository.record_match(track.spotify_track_id, match, status)
                    self.repository.put_waitlist(
                        track.spotify_track_id,
                        WaitlistReason.NOT_AVAILABLE_YET,
                        self.settings.waitlist_retry_days,
                    )
                summary.liked += 1
            elif status in {TrackStatus.LIKE_LIMIT_REACHED, TrackStatus.PREMIUM_REQUIRED}:
                if not self.settings.dry_run:
                    self.repository.record_action(track.spotify_track_id, ActionType.LIKE, status.value)
                    self.repository.record_match(track.spotify_track_id, match, status)
                    self.repository.put_waitlist(
                        track.spotify_track_id,
                        WaitlistReason.NOT_AVAILABLE_YET,
                        self.settings.waitlist_retry_days,
                    )
                summary.waitlisted += 1
            elif status == TrackStatus.ERROR:
                summary.errors += 1
                summary.problem_tracks.append(self._problem_track_label(track))

    def _retry_waitlist(self, summary: RunSummary) -> set[str]:
        track_ids = set(self.repository.get_waitlist_tracks_due())
        if not track_ids:
            LOGGER.info("No waitlist tracks are due for retry.")
            return set()

        tracks = self.spotify.get_liked_tracks()
        due_tracks = [track for track in tracks if track.spotify_track_id in track_ids]
        if not due_tracks:
            LOGGER.info("No due waitlist tracks are present in current Spotify liked tracks.")
            return set()

        self._run_tracks(due_tracks, summary)
        return {track.spotify_track_id for track in due_tracks}

    def _sync_downloads_cache(self, summary: RunSummary) -> None:
        LOGGER.info("Starting Soundeo downloads cache sync.")
        candidates = self.soundeo.refresh_downloaded_cache()
        rows = self.soundeo.to_download_cache_rows(candidates)
        cached = self.repository.replace_downloads_cache(rows)
        LOGGER.info("Soundeo downloads cache sync completed: %s cached tracks written to sqlite.", cached)
        summary.processed = cached
        summary.downloaded_already = cached

    def _problem_track_label(self, track: SpotifyTrack, detail: str = "") -> str:
        label = f"{track.artists_raw} - {track.title_raw} [{track.spotify_track_id}]"
        return f"{label}: {detail}" if detail else label
