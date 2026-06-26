from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.matching.normalizer import extract_remix, normalize_text, normalize_track
from app.models import MatchResult, SoundeoCandidate, SpotifyTrack

RELEASE_YEAR_TOLERANCE = 1
AVAILABLE_CANDIDATE_SCORE_MARGIN = 25
BASE_VARIANT_MARKERS = {"original", "extended", "edit"}
REMIX_VARIANT_MARKERS = {"remix", "rework", "vip"}
GENERIC_VARIANT_TOKENS = BASE_VARIANT_MARKERS | REMIX_VARIANT_MARKERS | {"mix", "version", "radio", "album"}


def _token_set_ratio(left: str, right: str) -> float:
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0

    common = " ".join(sorted(left_tokens & right_tokens))
    left_only = " ".join(sorted(left_tokens - right_tokens))
    right_only = " ".join(sorted(right_tokens - left_tokens))

    candidates = [
        (common, common),
        (common, f"{common} {left_only}".strip()),
        (common, f"{common} {right_only}".strip()),
        (f"{common} {left_only}".strip(), f"{common} {right_only}".strip()),
    ]
    return max(SequenceMatcher(None, a, b).ratio() for a, b in candidates) * 100


def _significant_tokens(value: str) -> set[str]:
    return {token for token in value.split() if len(token) >= 3}


def _artist_gate(track_artist: str, candidate_artists: str) -> bool:
    track_tokens = _significant_tokens(track_artist)
    candidate_tokens = _significant_tokens(candidate_artists)
    if not track_tokens or not candidate_tokens:
        return False
    return bool(track_tokens & candidate_tokens)


def _title_gate(track_title: str, candidate_title: str) -> bool:
    track_significant = _significant_tokens(track_title)
    candidate_significant = _significant_tokens(candidate_title)
    if track_significant and candidate_significant:
        track_tokens = track_significant
        candidate_tokens = candidate_significant
    else:
        track_tokens = set(track_title.split())
        candidate_tokens = set(candidate_title.split())
    if not track_tokens or not candidate_tokens:
        return False

    overlap = track_tokens & candidate_tokens
    if not overlap:
        return False

    required_overlap = min(len(track_tokens), len(candidate_tokens))
    if required_overlap <= 2:
        return len(overlap) >= 1
    if required_overlap <= 4:
        return len(overlap) >= 2
    return len(overlap) >= max(2, required_overlap // 2)


def _compatibility_gate(track: SpotifyTrack, candidate: SoundeoCandidate) -> bool:
    normalized = normalize_track(track)
    candidate_artist = normalize_text(candidate.artists)
    candidate_title = normalize_text(candidate.title)
    return (
        _track_artist_gate(normalized.artist, normalized.remix, candidate_artist, candidate)
        and _title_gate(normalized.title, candidate_title)
        and _variant_gate(normalized.remix, candidate)
        and _release_year_gate(track, candidate)
    )


def _variant_kind(value: str | None) -> str:
    if not value:
        return "base"
    normalized = normalize_text(value)
    tokens = set(normalized.split())
    if not (tokens - GENERIC_VARIANT_TOKENS):
        return "base"
    if tokens & REMIX_VARIANT_MARKERS:
        return "remix"
    if "mix" in tokens and not tokens & BASE_VARIANT_MARKERS:
        return "remix"
    if "version" in tokens and not tokens & BASE_VARIANT_MARKERS:
        return "remix"
    return "base"


def _candidate_variant(candidate: SoundeoCandidate) -> str | None:
    variant = extract_remix(candidate.title)
    if variant:
        return variant
    for label in candidate.extra_labels:
        variant = extract_remix(label)
        if variant:
            return variant
        normalized_label = normalize_text(label)
        if any(token in normalized_label for token in ("mix", "edit", "remix", "version", "vip", "rework")):
            return normalized_label
    return None


def _track_artist_gate(
    track_artist: str,
    track_variant: str | None,
    candidate_artists: str,
    candidate: SoundeoCandidate,
) -> bool:
    if _artist_gate(track_artist, candidate_artists):
        return True
    if not track_variant:
        return False
    if _variant_kind(track_variant) != "remix":
        return False

    candidate_variant = _candidate_variant(candidate)
    if not candidate_variant:
        return False

    track_artist_tokens = _significant_tokens(track_artist)
    candidate_variant_tokens = _significant_tokens(normalize_text(candidate_variant))
    return bool(track_artist_tokens & candidate_variant_tokens)


def _variant_gate(track_variant: str | None, candidate: SoundeoCandidate) -> bool:
    candidate_variant = _candidate_variant(candidate)
    track_kind = _variant_kind(track_variant)
    candidate_kind = _variant_kind(candidate_variant)

    if track_kind == "base" and candidate_kind == "remix":
        return False
    if track_kind == "remix" and candidate_kind == "base":
        return _variant_identity_matches(track_variant, candidate_variant)
    if track_kind == "remix" and candidate_kind == "remix":
        return _variant_identity_matches(track_variant, candidate_variant)
    return True


def _variant_identity_matches(track_variant: str | None, candidate_variant: str | None) -> bool:
    track_tokens = _variant_identity_tokens(track_variant)
    candidate_tokens = _variant_identity_tokens(candidate_variant)
    if track_tokens and candidate_tokens:
        return bool(track_tokens & candidate_tokens)
    return normalize_text(track_variant or "") == normalize_text(candidate_variant or "")


def _variant_identity_tokens(value: str | None) -> set[str]:
    return _significant_tokens(normalize_text(value or "")) - GENERIC_VARIANT_TOKENS


def score_candidate(track: SpotifyTrack, candidate: SoundeoCandidate) -> float:
    normalized = normalize_track(track)
    candidate_artist = normalize_text(candidate.artists)
    candidate_title = normalize_text(candidate.title)
    labels = " ".join(candidate.extra_labels)
    candidate_query = f"{candidate_artist} {candidate_title} {labels}".casefold().strip()
    score = _token_set_ratio(normalized.normalized_query, candidate_query)

    artist_score = _token_set_ratio(normalized.artist, candidate_artist)
    title_score = _token_set_ratio(normalized.title, candidate_title)
    score += artist_score * 0.25
    score += title_score * 0.35

    if candidate.duration_seconds and track.duration_ms:
        delta = abs(candidate.duration_seconds - round(track.duration_ms / 1000))
        if delta <= 2:
            score += 7
        elif delta <= 10:
            score += 3

    if track.release_name and candidate.release_name:
        if normalize_text(track.release_name) == normalize_text(candidate.release_name):
            score += 5

    track_year = _release_year(track.release_date)
    candidate_years = _candidate_years(candidate)
    if track_year and track_year in candidate_years:
        score += 4
    elif track_year and candidate_years:
        score -= 3

    if normalized.remix and normalized.remix in normalize_text(f"{candidate.title} {labels}"):
        score += 6

    if candidate.is_available:
        score += 2

    return score


def _release_year(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"\b(19|20)\d{2}\b", value)
    return match.group(0) if match else None


def _candidate_years(candidate: SoundeoCandidate) -> set[str]:
    value = " ".join(
        part
        for part in (
            candidate.title,
            candidate.artists,
            candidate.release_name or "",
            candidate.release_date or "",
            " ".join(candidate.extra_labels),
        )
        if part
    )
    return set(re.findall(r"\b(?:19|20)\d{2}\b", value))


def _release_year_gate(track: SpotifyTrack, candidate: SoundeoCandidate) -> bool:
    track_year = _release_year(track.release_date)
    candidate_years = _candidate_years(candidate)
    if not track_year or not candidate_years:
        return True
    track_year_int = int(track_year)
    return any(abs(track_year_int - int(candidate_year)) <= RELEASE_YEAR_TOLERANCE for candidate_year in candidate_years)


def pick_best_match(track: SpotifyTrack, candidates: list[SoundeoCandidate]) -> MatchResult:
    if not candidates:
        return MatchResult(candidate=None, score=0.0, match_type="no_match")

    compatible = [candidate for candidate in candidates if _compatibility_gate(track, candidate)]
    if not compatible:
        return MatchResult(candidate=None, score=0.0, match_type="no_match")

    scored = sorted(
        ((score_candidate(track, candidate), candidate) for candidate in compatible),
        key=lambda item: item[0],
        reverse=True,
    )
    best_score, best_candidate = scored[0]
    if not best_candidate.is_available:
        available_scored = [
            (score, candidate)
            for score, candidate in scored
            if candidate.is_available and score >= best_score - AVAILABLE_CANDIDATE_SCORE_MARGIN
        ]
        if available_scored:
            best_score, best_candidate = available_scored[0]
    if best_score < 85:
        return MatchResult(candidate=None, score=best_score, match_type="fuzzy_rejected")
    return MatchResult(candidate=best_candidate, score=best_score, match_type="fuzzy_high")
