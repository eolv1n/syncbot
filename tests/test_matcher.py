import unittest
from datetime import UTC, datetime

from app.matching.matcher import pick_best_match
from app.models import SoundeoCandidate, SpotifyTrack


def make_track() -> SpotifyTrack:
    return SpotifyTrack(
        spotify_track_id="1",
        artists_raw="Anyma",
        title_raw="Hypnotized (Extended Mix)",
        added_at=datetime(2026, 1, 1, tzinfo=UTC),
        duration_ms=210000,
        release_name="Genesys",
    )


class MatcherTests(unittest.TestCase):
    def test_pick_best_match_prefers_duration_and_availability(self) -> None:
        result = pick_best_match(
            make_track(),
            [
                SoundeoCandidate(
                    soundeo_track_id="a",
                    title="Hypnotized",
                    artists="Anyma",
                    duration_seconds=180,
                    is_available=False,
                ),
                SoundeoCandidate(
                    soundeo_track_id="b",
                    title="Hypnotized",
                    artists="Anyma",
                    duration_seconds=210,
                    release_name="Genesys",
                    is_available=True,
                    extra_labels=["extended mix"],
                ),
            ],
        )
        self.assertIsNotNone(result.candidate)
        assert result.candidate is not None
        self.assertEqual(result.candidate.soundeo_track_id, "b")
        self.assertGreaterEqual(result.score, 90)

    def test_pick_best_match_rejects_wrong_title_even_with_same_artist(self) -> None:
        track = SpotifyTrack(
            spotify_track_id="2",
            artists_raw="Guy J",
            title_raw="Worlds Apart",
            added_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        result = pick_best_match(
            track,
            [
                SoundeoCandidate(
                    soundeo_track_id="bad",
                    title="Surreal",
                    artists="Guy J",
                    is_available=True,
                )
            ],
        )
        self.assertIsNone(result.candidate)

    def test_pick_best_match_requires_artist_overlap(self) -> None:
        track = SpotifyTrack(
            spotify_track_id="3",
            artists_raw="Niko Ava",
            title_raw="Freedom",
            added_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        result = pick_best_match(
            track,
            [
                SoundeoCandidate(
                    soundeo_track_id="bad",
                    title="Freedom",
                    artists="Another Artist",
                    is_available=True,
                )
            ],
        )
        self.assertIsNone(result.candidate)


if __name__ == "__main__":
    unittest.main()
