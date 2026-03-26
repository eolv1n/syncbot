from __future__ import annotations

import re

from app.models import NormalizedTrack, SpotifyTrack


NOISE_PATTERNS = [
    r"\bradio edit\b",
    r"\bfeat\.?\b",
    r"\bft\.?\b",
]

REMIX_PATTERN = re.compile(r"\(([^)]*(mix|edit|remix|version|vip)[^)]*)\)", re.IGNORECASE)


def _clean_piece(value: str) -> str:
    value = value.casefold()
    value = value.replace("&", " and ")
    value = re.sub(r"[\[\]{}()\-_/,:;.!?]+", " ", value)
    for pattern in NOISE_PATTERNS:
        value = re.sub(pattern, " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def extract_remix(value: str) -> str | None:
    match = REMIX_PATTERN.search(value)
    if not match:
        return None
    remix = _clean_piece(match.group(1))
    return remix or None


def normalize_track(track: SpotifyTrack) -> NormalizedTrack:
    remix = extract_remix(track.title_raw)
    artist = _clean_piece(track.artists_raw)
    title = _clean_piece(track.title_raw)
    if remix:
        title = re.sub(rf"\b{re.escape(remix)}\b", " ", title).strip()
        title = re.sub(r"\s+", " ", title).strip()
    normalized_query = " ".join(part for part in (artist, title, remix) if part).strip()
    return NormalizedTrack(
        artist=artist,
        title=title,
        remix=remix,
        normalized_query=normalized_query,
        original=track,
    )
