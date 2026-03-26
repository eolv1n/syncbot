from __future__ import annotations

from difflib import SequenceMatcher

from app.matching.normalizer import normalize_track
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


def score_candidate(track: SpotifyTrack, candidate: SoundeoCandidate) -> float:
    normalized = normalize_track(track)
    labels = " ".join(candidate.extra_labels)
    candidate_query = f"{candidate.artists} {candidate.title} {labels}".casefold().strip()
    score = _token_set_ratio(normalized.normalized_query, candidate_query)

    if candidate.duration_seconds and track.duration_ms:
        delta = abs(candidate.duration_seconds - round(track.duration_ms / 1000))
        if delta <= 2:
            score += 7
        elif delta <= 10:
            score += 3

    if track.release_name and candidate.release_name:
        if track.release_name.casefold() == candidate.release_name.casefold():
            score += 5

    if normalized.remix and any(normalized.remix in label.casefold() for label in candidate.extra_labels):
        score += 6

    if candidate.is_available:
        score += 2

    return score


def pick_best_match(track: SpotifyTrack, candidates: list[SoundeoCandidate]) -> MatchResult:
    if not candidates:
        return MatchResult(candidate=None, score=0.0, match_type="no_match")

    scored = sorted(
        ((score_candidate(track, candidate), candidate) for candidate in candidates),
        key=lambda item: item[0],
        reverse=True,
    )
    best_score, best_candidate = scored[0]
    match_type = "fuzzy_high" if best_score >= 85 else "fuzzy_low"
    return MatchResult(candidate=best_candidate, score=best_score, match_type=match_type)
