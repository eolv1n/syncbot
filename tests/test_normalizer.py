import unittest
from datetime import UTC, datetime

from app.matching.normalizer import build_normalized_track_key, extract_remix, normalize_track
from app.models import SpotifyTrack


def make_track(title: str, artists: str = "Artist Name") -> SpotifyTrack:
    return SpotifyTrack(
        spotify_track_id="track-1",
        artists_raw=artists,
        title_raw=title,
        added_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


class NormalizerTests(unittest.TestCase):
    def test_extract_remix(self) -> None:
        self.assertEqual(extract_remix("Track Name (Extended Mix)"), "extended mix")

    def test_extract_remix_from_dash_suffix(self) -> None:
        self.assertEqual(extract_remix("Life Cycle - 4x4 Mix"), "4x4 mix")

    def test_normalize_track_removes_noise(self) -> None:
        normalized = normalize_track(make_track("Track Name (Original Mix) feat. Guest"))
        self.assertEqual(normalized.artist, "artist name")
        self.assertEqual(normalized.title, "track name guest")
        self.assertEqual(normalized.normalized_query, "artist name track name guest original mix")

    def test_normalized_key_folds_diacritics_and_quotes(self) -> None:
        spotify_key = normalize_track(make_track("Cafe Del Mar", artists="Energy 52")).normalized_query
        soundeo_key = build_normalized_track_key("Energy 52", "Café Del Mar", None)
        self.assertEqual(spotify_key, soundeo_key)

    def test_normalized_key_folds_artist_punctuation(self) -> None:
        spotify_key = normalize_track(make_track("Pulsar", artists="DJ Kon'")).normalized_query
        soundeo_key = build_normalized_track_key("DJ Kon’", "Pulsar", None)
        self.assertEqual(spotify_key, soundeo_key)


if __name__ == "__main__":
    unittest.main()
