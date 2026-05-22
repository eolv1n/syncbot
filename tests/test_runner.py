import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from app.config import AppSettings
from app.models import ActionType, MatchResult, SoundeoCandidate, SpotifyTrack, TrackStatus, WaitlistReason
from app.matching.normalizer import normalize_track
from app.services.runner import SyncRunner


def make_track(track_id: str, day: int) -> SpotifyTrack:
    return SpotifyTrack(
        spotify_track_id=track_id,
        artists_raw=f"Artist {track_id}",
        title_raw=f"Track {track_id}",
        added_at=datetime(2026, 1, day, tzinfo=UTC),
    )


class RunnerTests(unittest.TestCase):
    def _make_runner(self, tmpdir: str) -> tuple[AppSettings, SyncRunner]:
        with patch("app.services.runner.SpotifyClient"), patch("app.services.runner.SoundeoAutomation"):
            settings = AppSettings()
            settings.soundeo_voting_enabled = True
            settings.base_dir = Path(tmpdir)
            settings.data_dir = settings.base_dir / "data"
            settings.logs_dir = settings.base_dir / "logs"
            settings.artifacts_dir = settings.base_dir / "artifacts"
            settings.screenshots_dir = settings.artifacts_dir / "screenshots"
            settings.html_dir = settings.artifacts_dir / "html"
            settings.reports_dir = settings.artifacts_dir / "reports"
            settings.playwright_state_dir = settings.base_dir / "playwright" / ".auth"
            settings.sqlite_path = settings.data_dir / "app.db"
            settings.spotify_token_cache_path = settings.data_dir / "spotify_tokens.json"
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            settings.reports_dir.mkdir(parents=True, exist_ok=True)
            runner = SyncRunner(settings)
        return settings, runner

    def test_daily_sync_retries_waitlist_before_fresh_tracks_without_duplicates(self) -> None:
        with TemporaryDirectory() as tmpdir:
            settings, runner = self._make_runner(tmpdir)

            due_track = make_track("due-track", 1)
            fresh_track = make_track("fresh-track", 2)
            runner.repository.get_waitlist_tracks_due = MagicMock(return_value=["due-track"])
            runner.repository.set_state = MagicMock()
            runner.repository.export_summary = MagicMock(return_value=settings.reports_dir / "summary.json")
            runner.soundeo.close = MagicMock()
            runner._refresh_recent_downloads_cache = MagicMock(return_value=0)  # type: ignore[method-assign]
            runner.spotify.get_liked_tracks = MagicMock(side_effect=[[due_track, fresh_track], [due_track, fresh_track]])

            processed_batches: list[list[str]] = []

            def fake_run_tracks(tracks: list[SpotifyTrack], summary) -> None:
                processed_batches.append([track.spotify_track_id for track in tracks])

            runner._run_tracks = fake_run_tracks  # type: ignore[method-assign]
            runner._daily_after = MagicMock(return_value=datetime(2025, 12, 31, tzinfo=UTC))

            exit_code = runner.run("daily-sync")

            self.assertEqual(exit_code, 0)
            runner._refresh_recent_downloads_cache.assert_called_once()
            self.assertEqual(processed_batches, [["due-track"], ["fresh-track"]])
            self.assertEqual(runner.spotify.get_liked_tracks.call_count, 2)
            self.assertIsNone(runner.spotify.get_liked_tracks.call_args_list[0].kwargs.get("after"))
            self.assertEqual(
                runner.spotify.get_liked_tracks.call_args_list[1].kwargs.get("after"),
                datetime(2025, 12, 31, tzinfo=UTC),
            )

    def test_fresh_sync_skips_waitlist_retry(self) -> None:
        with TemporaryDirectory() as tmpdir:
            settings, runner = self._make_runner(tmpdir)

            fresh_track = make_track("fresh-track", 2)
            runner.repository.get_waitlist_tracks_due = MagicMock(return_value=["due-track"])
            runner.repository.set_state = MagicMock()
            runner.repository.export_summary = MagicMock(return_value=settings.reports_dir / "summary.json")
            runner.soundeo.close = MagicMock()
            runner._refresh_recent_downloads_cache = MagicMock(return_value=0)  # type: ignore[method-assign]
            runner.spotify.get_liked_tracks = MagicMock(return_value=[fresh_track])
            runner._run_tracks = MagicMock()  # type: ignore[method-assign]
            runner._daily_after = MagicMock(return_value=datetime(2025, 12, 31, tzinfo=UTC))

            exit_code = runner.run("fresh-sync")

            self.assertEqual(exit_code, 0)
            runner.repository.get_waitlist_tracks_due.assert_not_called()
            runner.spotify.get_liked_tracks.assert_called_once_with(after=datetime(2025, 12, 31, tzinfo=UTC))
            self.assertEqual(runner._run_tracks.call_args.args[0], [fresh_track])

    def test_sync_track_ids_only_runs_requested_liked_tracks(self) -> None:
        with TemporaryDirectory() as tmpdir:
            settings, runner = self._make_runner(tmpdir)

            wanted = make_track("wanted-track", 1)
            other = make_track("other-track", 2)
            runner.repository.export_summary = MagicMock(return_value=settings.reports_dir / "summary.json")
            runner.soundeo.close = MagicMock()
            runner._refresh_recent_downloads_cache = MagicMock(return_value=0)  # type: ignore[method-assign]
            runner.spotify.get_liked_tracks = MagicMock(return_value=[wanted, other])
            runner._run_tracks = MagicMock()  # type: ignore[method-assign]

            exit_code = runner.run_track_ids({"wanted-track"})

            self.assertEqual(exit_code, 0)
            runner._run_tracks.assert_called_once()
            self.assertEqual(runner._run_tracks.call_args.args[0], [wanted])

    def test_downloads_preflight_upserts_first_downloads_page(self) -> None:
        with TemporaryDirectory() as tmpdir:
            _, runner = self._make_runner(tmpdir)
            candidate = SoundeoCandidate(
                soundeo_track_id="soundeo-downloaded",
                title="Track Name",
                artists="Artist Name",
                is_downloaded=True,
            )
            rows = [("soundeo-downloaded", "artist name track name", None)]

            runner.soundeo.refresh_recent_downloaded_cache = MagicMock(return_value=[candidate])
            runner.soundeo.to_download_cache_rows = MagicMock(return_value=rows)

            cached = runner._refresh_recent_downloads_cache()

            self.assertEqual(cached, 1)
            runner.soundeo.refresh_recent_downloaded_cache.assert_called_once_with()
            runner.soundeo.to_download_cache_rows.assert_called_once_with([candidate])
            self.assertTrue(runner.repository.is_soundeo_track_downloaded("soundeo-downloaded"))

    def test_like_limit_goes_to_waitlist_instead_of_error(self) -> None:
        with TemporaryDirectory() as tmpdir:
            _, runner = self._make_runner(tmpdir)
            track = make_track("limited-track", 1)
            normalized = normalize_track(track)
            summary = runner.run.__globals__["RunSummary"](mode="daily-sync")
            match = MatchResult(
                candidate=SoundeoCandidate(
                    soundeo_track_id="soundeo-1",
                    title=track.title_raw,
                    artists=track.artists_raw,
                    is_available=False,
                ),
                score=95.0,
                match_type="fuzzy_high",
            )

            runner.repository.is_track_downloaded = MagicMock(return_value=False)
            runner.repository.is_soundeo_track_downloaded = MagicMock(return_value=False)
            runner.repository.was_action_recorded = MagicMock(return_value=False)
            runner.repository.record_action = MagicMock()
            runner.repository.record_match = MagicMock()
            runner.repository.put_waitlist = MagicMock()
            runner.soundeo.search_track = MagicMock(return_value=[match.candidate])
            runner.soundeo.apply_action = MagicMock(return_value=TrackStatus.LIKE_LIMIT_REACHED)

            runner._process_track(track, normalized, summary)

            self.assertEqual(summary.waitlisted, 1)
            self.assertEqual(summary.errors, 0)
            runner.repository.record_action.assert_called_once_with(
                track.spotify_track_id,
                ActionType.LIKE,
                TrackStatus.LIKE_LIMIT_REACHED.value,
            )

    def test_voting_disabled_waitlists_without_soundeo_action(self) -> None:
        with TemporaryDirectory() as tmpdir:
            _, runner = self._make_runner(tmpdir)
            runner.settings.soundeo_voting_enabled = False
            track = make_track("premium-track", 1)
            normalized = normalize_track(track)
            summary = runner.run.__globals__["RunSummary"](mode="fresh-sync")
            candidate = SoundeoCandidate(
                soundeo_track_id="soundeo-premium",
                title=track.title_raw,
                artists=track.artists_raw,
                is_available=False,
            )
            match = MatchResult(candidate=candidate, score=95.0, match_type="fuzzy_high")

            runner.repository.is_track_downloaded = MagicMock(return_value=False)
            runner.repository.is_soundeo_track_downloaded = MagicMock(return_value=False)
            runner.repository.record_action = MagicMock()
            runner.repository.record_match = MagicMock()
            runner.repository.put_waitlist = MagicMock()
            runner.soundeo.search_track = MagicMock(return_value=[candidate])
            runner.soundeo.apply_action = MagicMock()

            runner._process_track(track, normalized, summary)

            self.assertEqual(summary.waitlisted, 1)
            runner.soundeo.apply_action.assert_not_called()
            runner.repository.record_action.assert_called_once_with(
                track.spotify_track_id,
                ActionType.LIKE,
                TrackStatus.PREMIUM_REQUIRED.value,
            )
            match_args = runner.repository.record_match.call_args.args
            self.assertEqual(match_args[0], track.spotify_track_id)
            self.assertIsNotNone(match_args[1].candidate)
            self.assertEqual(match_args[1].candidate.soundeo_track_id, candidate.soundeo_track_id)
            self.assertEqual(match_args[2], TrackStatus.PREMIUM_REQUIRED)

    def test_problem_tracks_are_human_readable(self) -> None:
        with TemporaryDirectory() as tmpdir:
            _, runner = self._make_runner(tmpdir)
            track = SpotifyTrack(
                spotify_track_id="spotify-123",
                artists_raw="Artist Name",
                title_raw="Track Name",
                added_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            summary = runner.run.__globals__["RunSummary"](mode="daily-sync")
            runner.repository.upsert_spotify_track = MagicMock(return_value=False)
            runner._process_track = MagicMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

            runner._run_tracks([track], summary)

            self.assertEqual(summary.errors, 1)
            self.assertEqual(summary.problem_tracks, ["Artist Name - Track Name [spotify-123]: boom"])

    def test_manual_review_waitlist_is_excluded_from_due_retry(self) -> None:
        with TemporaryDirectory() as tmpdir:
            _, runner = self._make_runner(tmpdir)
            old_track = SpotifyTrack(
                spotify_track_id="old-track",
                artists_raw="Old Artist",
                title_raw="Old Track",
                added_at=datetime.now(UTC) - timedelta(days=500),
            )
            runner.repository.upsert_spotify_track(old_track, normalize_track(old_track))
            runner.repository.put_waitlist(old_track.spotify_track_id, WaitlistReason.NOT_FOUND, 1)

            changed = runner.repository.mark_old_waitlist_for_manual_review(365, "test_old_waitlist")

            self.assertEqual(changed, 1)
            self.assertEqual(runner.repository.get_waitlist_tracks_due(), [])
            report = runner.repository.waitlist_report(older_than_days=365, status="manual_review")
            self.assertEqual(len(report), 1)
            self.assertEqual(report[0]["spotify_track_id"], "old-track")

    def test_refresh_spotify_metadata_does_not_touch_soundeo(self) -> None:
        with TemporaryDirectory() as tmpdir:
            settings, runner = self._make_runner(tmpdir)
            track = SpotifyTrack(
                spotify_track_id="metadata-track",
                artists_raw="Artist Name",
                title_raw="Track Name",
                added_at=datetime(2026, 1, 1, tzinfo=UTC),
                release_date="2024-01-01",
            )
            runner.spotify.get_liked_tracks = MagicMock(return_value=[track])
            runner.soundeo.refresh_recent_downloaded_cache = MagicMock()
            runner.repository.export_summary = MagicMock(return_value=settings.reports_dir / "summary.json")
            runner.soundeo.close = MagicMock()

            exit_code = runner.run("refresh-spotify-metadata")

            self.assertEqual(exit_code, 0)
            runner.spotify.get_liked_tracks.assert_called_once_with()
            runner.soundeo.refresh_recent_downloaded_cache.assert_not_called()
            report = runner.repository.waitlist_report()
            self.assertEqual(report, [])

    def test_waitlist_report_includes_last_soundeo_release_metadata(self) -> None:
        with TemporaryDirectory() as tmpdir:
            _, runner = self._make_runner(tmpdir)
            track = make_track("waitlisted-track", 1)
            match = MatchResult(
                candidate=SoundeoCandidate(
                    soundeo_track_id="soundeo-1",
                    title="Track waitlisted-track",
                    artists="Artist waitlisted-track",
                    release_name="Original Release",
                    release_date="2025-03-14",
                ),
                score=91.0,
                match_type="fuzzy_high",
            )
            runner.repository.upsert_spotify_track(track, normalize_track(track))
            runner.repository.record_match(track.spotify_track_id, match, TrackStatus.LIKED_WAITING_AVAILABILITY)
            runner.repository.put_waitlist(track.spotify_track_id, WaitlistReason.NOT_AVAILABLE_YET, 1)

            report = runner.repository.waitlist_report()

            self.assertEqual(report[0]["last_match_release_name"], "Original Release")
            self.assertEqual(report[0]["last_match_release_date"], "2025-03-14")


if __name__ == "__main__":
    unittest.main()
