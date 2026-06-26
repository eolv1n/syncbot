import unittest
from datetime import UTC, datetime

from app.config import AppSettings
from app.integrations.soundeo import SoundeoAutomation
from app.matching.normalizer import normalize_track
from app.models import SpotifyTrack


def make_track(artists: str, title: str) -> SpotifyTrack:
    return SpotifyTrack(
        spotify_track_id="track-1",
        artists_raw=artists,
        title_raw=title,
        added_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


class SoundeoSearchQueryTests(unittest.TestCase):
    def _queries(self, artists: str, title: str) -> list[str]:
        automation = SoundeoAutomation(AppSettings())
        return automation._search_queries(normalize_track(make_track(artists, title)))  # noqa: SLF001

    def test_search_queries_include_last_name_alias_for_two_word_artist(self) -> None:
        queries = self._queries("Eric Luttrell", "Generate - Extended Mix")

        self.assertIn("Luttrell Generate", queries)
        self.assertIn("Luttrell - Generate", queries)

    def test_search_queries_include_individual_artists_with_base_title(self) -> None:
        queries = self._queries("Space Motion, RIKO & GUGGA", "Just Groove - Radio Mix")

        self.assertIn("GUGGA Just Groove", queries)
        self.assertIn("GUGGA - Just Groove", queries)
        self.assertIn("Space Motion Just Groove", queries)

    def test_search_queries_include_artist_prefix_without_feature_artist(self) -> None:
        queries = self._queries("Hot Since 82, Avision, Martha Wash", "In The Air")

        self.assertIn("Hot Since 82, Avision In The Air", queries)
        self.assertIn("Hot Since 82, Avision - In The Air", queries)

    def test_search_queries_strip_mixed_suffix_for_artist_fallbacks(self) -> None:
        queries = self._queries("Jon Gurd, Reset Robot", "Can U Feel It - Mixed")

        self.assertIn("Reset Robot Can U Feel It", queries)
        self.assertIn("Reset Robot - Can U Feel It", queries)

    def test_search_queries_strip_catalog_code_for_artist_fallbacks(self) -> None:
        queries = self._queries(
            "Paul Thomas, Weird Sounding Dude",
            "Summersault (ULF019) - Weird Sounding Dude Remix",
        )

        self.assertIn("Paul Thomas Summersault", queries)
        self.assertIn("Paul Thomas - Summersault", queries)
        self.assertIn("Weird Sounding Dude Summersault", queries)


if __name__ == "__main__":
    unittest.main()
