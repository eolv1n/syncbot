from __future__ import annotations

import logging
import time
from pathlib import Path

from app.config import AppSettings
from app.models import ActionType, MatchResult, NormalizedTrack, SoundeoCandidate, TrackStatus

LOGGER = logging.getLogger(__name__)


class SoundeoAutomation:
    def __init__(self, settings: AppSettings):
        self.settings = settings

    def refresh_downloaded_cache(self) -> list[SoundeoCandidate]:
        LOGGER.info("Downloaded cache refresh is scaffolded and requires site selectors.")
        return []

    def search_track(self, normalized: NormalizedTrack) -> list[SoundeoCandidate]:
        LOGGER.info("Searching Soundeo for '%s'", normalized.normalized_query)
        time.sleep(self.settings.rate_limit_seconds)
        return []

    def apply_action(self, match: MatchResult, action_type: ActionType) -> TrackStatus:
        if action_type == ActionType.STAR:
            return TrackStatus.STARRED
        if action_type == ActionType.LIKE:
            return TrackStatus.LIKED_WAITING_AVAILABILITY
        if action_type == ActionType.WAITLIST_ADD:
            return TrackStatus.NOT_FOUND_WAITLIST
        return TrackStatus.SKIPPED

    def capture_failure_artifacts(self, slug: str, html: str = "") -> tuple[Path, Path]:
        screenshot = self.settings.screenshots_dir / f"{slug}.png"
        html_path = self.settings.html_dir / f"{slug}.html"
        screenshot.write_bytes(b"")
        html_path.write_text(html, encoding="utf-8")
        return screenshot, html_path

