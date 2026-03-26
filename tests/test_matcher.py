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


if __name__ == "__main__":
    unittest.main()
