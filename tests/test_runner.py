import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from app.config import AppSettings
from app.models import ActionType, MatchResult, SoundeoCandidate, SpotifyTrack, TrackStatus
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
            runner.spotify.get_liked_tracks = MagicMock(side_effect=[[due_track, fresh_track], [due_track, fresh_track]])

            processed_batches: list[list[str]] = []

            def fake_run_tracks(tracks: list[SpotifyTrack], summary) -> None:
                processed_batches.append([track.spotify_track_id for track in tracks])

            runner._run_tracks = fake_run_tracks  # type: ignore[method-assign]
            runner._daily_after = MagicMock(return_value=datetime(2025, 12, 31, tzinfo=UTC))

            exit_code = runner.run("daily-sync")

            self.assertEqual(exit_code, 0)
            self.assertEqual(processed_batches, [["due-track"], ["fresh-track"]])
            self.assertEqual(runner.spotify.get_liked_tracks.call_count, 2)
            self.assertIsNone(runner.spotify.get_liked_tracks.call_args_list[0].kwargs.get("after"))
            self.assertEqual(
                runner.spotify.get_liked_tracks.call_args_list[1].kwargs.get("after"),
                datetime(2025, 12, 31, tzinfo=UTC),
            )

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


if __name__ == "__main__":
    unittest.main()
