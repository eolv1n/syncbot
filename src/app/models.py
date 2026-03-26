from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class TrackStatus(StrEnum):
    DOWNLOADED_ALREADY = "downloaded_already"
    STARRED = "starred"
    LIKED_WAITING_AVAILABILITY = "liked_waiting_availability"
    NOT_FOUND_WAITLIST = "not_found_waitlist"
    SKIPPED = "skipped"
    ERROR = "error"


class ActionType(StrEnum):
    STAR = "star"
    LIKE = "like"
    SKIP = "skip"
    WAITLIST_ADD = "waitlist_add"


class WaitlistReason(StrEnum):
    NOT_FOUND = "not_found"
    NOT_AVAILABLE_YET = "not_available_yet"


@dataclass(slots=True)
class SpotifyTrack:
    spotify_track_id: str
    artists_raw: str
    title_raw: str
    added_at: datetime
    duration_ms: int | None = None
    isrc: str | None = None
    release_name: str | None = None


@dataclass(slots=True)
class NormalizedTrack:
    artist: str
    title: str
    remix: str | None
    normalized_query: str
    original: SpotifyTrack


@dataclass(slots=True)
class SoundeoCandidate:
    soundeo_track_id: str
    title: str
    artists: str
    duration_seconds: int | None = None
    release_name: str | None = None
    is_available: bool = False
    is_downloaded: bool = False
    url: str | None = None
    extra_labels: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MatchResult:
    candidate: SoundeoCandidate | None
    score: float
    match_type: str


@dataclass(slots=True)
class RunSummary:
    mode: str
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    processed: int = 0
    newly_seen: int = 0
    downloaded_already: int = 0
    starred: int = 0
    liked: int = 0
    waitlisted: int = 0
    errors: int = 0
    problem_tracks: list[str] = field(default_factory=list)

