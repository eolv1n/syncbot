from __future__ import annotations

from difflib import SequenceMatcher

from app.matching.normalizer import extract_remix, normalize_text, normalize_track
from app.models import MatchResult, SoundeoCandidate, SpotifyTrack


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
    track_tokens = _significant_tokens(track_title)
    candidate_tokens = _significant_tokens(candidate_title)
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
        _artist_gate(normalized.artist, candidate_artist)
        and _title_gate(normalized.title, candidate_title)
        and _variant_gate(normalized.remix, candidate)
    )


def _variant_kind(value: str | None) -> str:
    if not value:
        return "none"
    normalized = normalize_text(value)
    if "original" in normalized and not any(token in normalized for token in ("remix", "edit", "rework", "vip")):
        return "original"
    return "alternate"


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


def _variant_gate(track_variant: str | None, candidate: SoundeoCandidate) -> bool:
    candidate_variant = _candidate_variant(candidate)
    track_kind = _variant_kind(track_variant)
    candidate_kind = _variant_kind(candidate_variant)

    if track_kind in {"none", "original"} and candidate_kind == "alternate":
        return False
    if track_kind == "alternate" and candidate_kind in {"none", "original"}:
        return False
    if track_kind == "alternate" and candidate_kind == "alternate":
        track_tokens = _significant_tokens(normalize_text(track_variant or ""))
        candidate_tokens = _significant_tokens(normalize_text(candidate_variant or ""))
        return bool(track_tokens & candidate_tokens)
    return True


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

    if normalized.remix and normalized.remix in normalize_text(f"{candidate.title} {labels}"):
        score += 6

    if candidate.is_available:
        score += 2

    return score


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
    if best_score < 85:
        return MatchResult(candidate=None, score=best_score, match_type="fuzzy_rejected")
    return MatchResult(candidate=best_candidate, score=best_score, match_type="fuzzy_high")
