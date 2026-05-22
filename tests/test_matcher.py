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

    def test_pick_best_match_allows_remixer_artist_in_candidate_variant(self) -> None:
        track = SpotifyTrack(
            spotify_track_id="remixer-as-artist",
            artists_raw="Spencer Brown",
            title_raw="On The Moon - Spencer Brown Remix",
            added_at=datetime(2026, 1, 1, tzinfo=UTC),
            release_date="2017-04-21",
        )
        result = pick_best_match(
            track,
            [
                SoundeoCandidate(
                    soundeo_track_id="soundeo-remix",
                    title="On The Moon (Spencer Brown Remix)",
                    artists="Oliver Smith",
                    release_date="2017-04-21",
                    is_available=True,
                )
            ],
        )
        self.assertIsNotNone(result.candidate)
        assert result.candidate is not None
        self.assertEqual(result.candidate.soundeo_track_id, "soundeo-remix")

    def test_pick_best_match_rejects_remix_for_original_track(self) -> None:
        track = SpotifyTrack(
            spotify_track_id="4",
            artists_raw="Sasha",
            title_raw="Trigonometry",
            added_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        result = pick_best_match(
            track,
            [
                SoundeoCandidate(
                    soundeo_track_id="remix",
                    title="Trigonometry (La Fleur Remix)",
                    artists="Sasha",
                    is_available=True,
                )
            ],
        )
        self.assertIsNone(result.candidate)

    def test_pick_best_match_rejects_original_for_remix_track(self) -> None:
        track = SpotifyTrack(
            spotify_track_id="5",
            artists_raw="Energy 52",
            title_raw="Cafe Del Mar - Tale Of Us Renaissance Remix",
            added_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        result = pick_best_match(
            track,
            [
                SoundeoCandidate(
                    soundeo_track_id="original",
                    title="Cafe Del Mar (Original Mix)",
                    artists="Energy 52",
                    is_available=True,
                )
            ],
        )
        self.assertIsNone(result.candidate)

    def test_pick_best_match_allows_original_for_extended_suffix_track(self) -> None:
        track = SpotifyTrack(
            spotify_track_id="extended-suffix",
            artists_raw="Sasha, Artche",
            title_raw="Hold On - Extended",
            added_at=datetime(2026, 1, 1, tzinfo=UTC),
            release_date="2025-03-14",
        )
        result = pick_best_match(
            track,
            [
                SoundeoCandidate(
                    soundeo_track_id="original",
                    title="Hold On (Original Mix)",
                    artists="Sasha, Artche",
                    release_date="2025-03-14",
                    is_available=True,
                )
            ],
        )
        self.assertIsNotNone(result.candidate)
        assert result.candidate is not None
        self.assertEqual(result.candidate.soundeo_track_id, "original")

    def test_pick_best_match_allows_original_for_radio_mix_track(self) -> None:
        track = SpotifyTrack(
            spotify_track_id="radio-mix",
            artists_raw="Coeus",
            title_raw="Exploration - Radio Mix",
            added_at=datetime(2026, 1, 1, tzinfo=UTC),
            release_date="2026-01-30",
        )
        result = pick_best_match(
            track,
            [
                SoundeoCandidate(
                    soundeo_track_id="original",
                    title="Exploration (Original Mix)",
                    artists="Coeus",
                    release_date="2026-01-30",
                    is_available=True,
                )
            ],
        )
        self.assertIsNotNone(result.candidate)
        assert result.candidate is not None
        self.assertEqual(result.candidate.soundeo_track_id, "original")

    def test_pick_best_match_handles_short_title_tokens(self) -> None:
        track = SpotifyTrack(
            spotify_track_id="short-title",
            artists_raw="Volkoder",
            title_raw="So Am I",
            added_at=datetime(2026, 1, 1, tzinfo=UTC),
            release_date="2026-02-06",
        )
        result = pick_best_match(
            track,
            [
                SoundeoCandidate(
                    soundeo_track_id="original",
                    title="So Am I (Original Mix)",
                    artists="Volkoder",
                    release_date="2026-02-06",
                    is_available=True,
                )
            ],
        )
        self.assertIsNotNone(result.candidate)
        assert result.candidate is not None
        self.assertEqual(result.candidate.soundeo_track_id, "original")

    def test_pick_best_match_prefers_available_candidate_with_close_score(self) -> None:
        track = SpotifyTrack(
            spotify_track_id="available-close",
            artists_raw="Jon Gurd, Reset Robot",
            title_raw="Can U Feel It - Mixed",
            added_at=datetime(2026, 1, 1, tzinfo=UTC),
            release_date="2026-01-30",
        )
        result = pick_best_match(
            track,
            [
                SoundeoCandidate(
                    soundeo_track_id="vote-only",
                    title="Can U Feel It (Extended Mix)",
                    artists="Jon Gurd, Reset Robot",
                    release_date="2026-01-30",
                    is_available=False,
                ),
                SoundeoCandidate(
                    soundeo_track_id="available",
                    title="Can U Feel It (Extended Mix)",
                    artists="Jon Gurd, Reset Robot",
                    release_date="2025-07-11",
                    is_available=True,
                ),
            ],
        )
        self.assertIsNotNone(result.candidate)
        assert result.candidate is not None
        self.assertEqual(result.candidate.soundeo_track_id, "available")

    def test_pick_best_match_rejects_named_mix_for_extended_suffix_track(self) -> None:
        track = SpotifyTrack(
            spotify_track_id="extended-suffix",
            artists_raw="Sasha, Artche",
            title_raw="Hold On - Extended",
            added_at=datetime(2026, 1, 1, tzinfo=UTC),
            release_date="2025-03-14",
        )
        result = pick_best_match(
            track,
            [
                SoundeoCandidate(
                    soundeo_track_id="artche-mix",
                    title="Hold On (Artche Mix)",
                    artists="Sasha, Artche",
                    release_date="2025-03-14",
                    is_available=True,
                )
            ],
        )
        self.assertIsNone(result.candidate)

    def test_pick_best_match_rejects_different_remix_names(self) -> None:
        track = SpotifyTrack(
            spotify_track_id="remix-track",
            artists_raw="Energy 52",
            title_raw="Cafe Del Mar - Tale Of Us Renaissance Remix",
            added_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        result = pick_best_match(
            track,
            [
                SoundeoCandidate(
                    soundeo_track_id="wrong-remix",
                    title="Cafe Del Mar (Spencer Brown Remix)",
                    artists="Energy 52",
                    is_available=True,
                )
            ],
        )
        self.assertIsNone(result.candidate)

    def test_release_year_boosts_matching_candidate(self) -> None:
        track = SpotifyTrack(
            spotify_track_id="6",
            artists_raw="Artist",
            title_raw="Mirror",
            added_at=datetime(2026, 1, 1, tzinfo=UTC),
            release_date="2024-05-10",
        )
        result = pick_best_match(
            track,
            [
                SoundeoCandidate(
                    soundeo_track_id="old",
                    title="Mirror",
                    artists="Artist",
                    extra_labels=["Released 2020"],
                    is_available=True,
                ),
                SoundeoCandidate(
                    soundeo_track_id="new",
                    title="Mirror",
                    artists="Artist",
                    extra_labels=["Released 2024"],
                    is_available=True,
                ),
            ],
        )
        self.assertIsNotNone(result.candidate)
        assert result.candidate is not None
        self.assertEqual(result.candidate.soundeo_track_id, "new")

    def test_release_year_mismatch_rejects_candidate(self) -> None:
        track = SpotifyTrack(
            spotify_track_id="7",
            artists_raw="Artist",
            title_raw="Mirror",
            added_at=datetime(2026, 1, 1, tzinfo=UTC),
            release_date="2024-05-10",
        )
        result = pick_best_match(
            track,
            [
                SoundeoCandidate(
                    soundeo_track_id="reissue",
                    title="Mirror",
                    artists="Artist",
                    release_date="2020-01-01",
                    is_available=True,
                )
            ],
        )
        self.assertIsNone(result.candidate)

    def test_release_year_tolerance_allows_neighbor_year(self) -> None:
        track = SpotifyTrack(
            spotify_track_id="8",
            artists_raw="Artist",
            title_raw="Mirror",
            added_at=datetime(2026, 1, 1, tzinfo=UTC),
            release_date="2024-12-20",
        )
        result = pick_best_match(
            track,
            [
                SoundeoCandidate(
                    soundeo_track_id="neighbor",
                    title="Mirror",
                    artists="Artist",
                    release_date="2025-01-05",
                    is_available=True,
                )
            ],
        )
        self.assertIsNotNone(result.candidate)
        assert result.candidate is not None
        self.assertEqual(result.candidate.soundeo_track_id, "neighbor")


if __name__ == "__main__":
    unittest.main()
