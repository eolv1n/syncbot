from __future__ import annotations

import logging
from datetime import UTC, datetime

import requests

from app.config import AppSettings
from app.models import SpotifyTrack

LOGGER = logging.getLogger(__name__)


class SpotifyClient:
    SAVED_TRACKS_URL = "https://api.spotify.com/v1/me/tracks"

    def __init__(self, settings: AppSettings):
        self.settings = settings

    def get_liked_tracks(self, after: datetime | None = None) -> list[SpotifyTrack]:
        token = self._resolve_access_token()
        if not token:
            LOGGER.warning("Spotify token is not configured; returning no tracks.")
            return []

        headers = {"Authorization": f"Bearer {token}"}
        params = {"limit": self.settings.spotify_page_size}
        items: list[SpotifyTrack] = []
        next_url: str | None = self.SAVED_TRACKS_URL

        while next_url:
            response = requests.get(next_url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            payload = response.json()

            for item in payload.get("items", []):
                added_at = datetime.fromisoformat(item["added_at"].replace("Z", "+00:00")).astimezone(UTC)
                if after and added_at <= after:
                    continue
                track_data = item["track"]
                items.append(
                    SpotifyTrack(
                        spotify_track_id=track_data["id"],
                        artists_raw=", ".join(artist["name"] for artist in track_data.get("artists", [])),
                        title_raw=track_data["name"],
                        added_at=added_at,
                        duration_ms=track_data.get("duration_ms"),
                        isrc=track_data.get("external_ids", {}).get("isrc"),
                        release_name=track_data.get("album", {}).get("name"),
                    )
                )

            next_url = payload.get("next")
            params = None

        return items

    def _resolve_access_token(self) -> str | None:
        return self.settings.spotify_access_token
