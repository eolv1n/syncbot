from __future__ import annotations

import base64
import json
import logging
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event
from urllib.parse import parse_qs, urlencode, urlparse
from datetime import UTC, datetime
import requests
from requests import Response

from app.config import AppSettings
from app.models import SpotifyTrack

LOGGER = logging.getLogger(__name__)


class SpotifyClient:
    SAVED_TRACKS_URL = "https://api.spotify.com/v1/me/tracks"
    TOKEN_URL = "https://accounts.spotify.com/api/token"

    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.session = requests.Session()

    def get_liked_tracks(self, after: datetime | None = None) -> list[SpotifyTrack]:
        token = self._resolve_access_token()
        if not token:
            LOGGER.warning("Spotify token is not configured; returning no tracks.")
            return []

        headers = {"Authorization": f"Bearer {token}"}
        params = {"limit": self.settings.spotify_page_size}
        items: list[SpotifyTrack] = []
        next_url: str | None = self.SAVED_TRACKS_URL
        reached_older_tracks = False

        while next_url and not reached_older_tracks:
            response = self._request_with_retries(
                method="GET",
                url=next_url,
                headers=headers,
                params=params,
            )
            response.raise_for_status()
            payload = response.json()

            for item in payload.get("items", []):
                added_at = datetime.fromisoformat(item["added_at"].replace("Z", "+00:00")).astimezone(UTC)
                if after and added_at <= after:
                    reached_older_tracks = True
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

            next_url = None if reached_older_tracks else payload.get("next")
            params = None

        return items

    def _resolve_access_token(self) -> str | None:
        cached = self._load_cached_tokens()
        if cached:
            access_token = cached.get("access_token")
            expires_at = cached.get("expires_at")
            if access_token and not self._is_expired(expires_at):
                return str(access_token)

            refresh_token = cached.get("refresh_token") or self.settings.spotify_refresh_token
            if refresh_token:
                refreshed = self._refresh_access_token(str(refresh_token))
                if refreshed:
                    return refreshed.get("access_token")

        if self.settings.spotify_access_token:
            return self.settings.spotify_access_token
        if self.settings.spotify_refresh_token:
            refreshed = self._refresh_access_token(self.settings.spotify_refresh_token)
            if refreshed:
                return refreshed.get("access_token")
        return None

    def _load_cached_tokens(self) -> dict[str, object] | None:
        path = self.settings.spotify_token_cache_path
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            LOGGER.warning("Spotify token cache at %s is invalid JSON.", path)
            return None

    def _save_cached_tokens(self, payload: dict[str, object]) -> None:
        current = self._load_cached_tokens() or {}
        merged = current | payload
        self.settings.spotify_token_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings.spotify_token_cache_path.write_text(
            json.dumps(merged, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _is_expired(self, expires_at: object) -> bool:
        if not expires_at or not isinstance(expires_at, str):
            return True
        try:
            dt = datetime.fromisoformat(expires_at)
        except ValueError:
            return True
        return datetime.now(UTC) >= dt

    def _refresh_access_token(self, refresh_token: str) -> dict[str, str] | None:
        if not self.settings.spotify_client_id or not self.settings.spotify_client_secret:
            LOGGER.warning("Spotify client credentials are missing; cannot refresh token.")
            return None

        response = self._request_with_retries(
            method="POST",
            url=self.TOKEN_URL,
            headers=self._token_headers(),
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        if response.status_code >= 400:
            LOGGER.warning("Spotify refresh token request failed: %s", response.text)
            return None

        payload = response.json()
        result = self._normalize_token_payload(payload, refresh_token=refresh_token)
        self._save_cached_tokens(result)
        return result

    def _normalize_token_payload(
        self,
        payload: dict[str, object],
        refresh_token: str | None = None,
    ) -> dict[str, str]:
        expires_in = int(payload.get("expires_in", 3600))
        expires_at = datetime.now(UTC).timestamp() + max(expires_in - 60, 0)
        return {
            "access_token": str(payload["access_token"]),
            "refresh_token": str(payload.get("refresh_token") or refresh_token or ""),
            "scope": str(payload.get("scope") or self.settings.spotify_scope),
            "token_type": str(payload.get("token_type") or "Bearer"),
            "expires_at": datetime.fromtimestamp(expires_at, tz=UTC).isoformat(),
        }

    def _token_headers(self) -> dict[str, str]:
        raw = f"{self.settings.spotify_client_id}:{self.settings.spotify_client_secret}"
        encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")
        return {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def _request_with_retries(self, method: str, url: str, **kwargs: object) -> Response:
        attempts = self.settings.spotify_request_retry_count + 1
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                return self.session.request(
                    method=method,
                    url=url,
                    timeout=self.settings.spotify_request_timeout_seconds,
                    **kwargs,
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= attempts:
                    break
                sleep_for = self.settings.spotify_request_retry_backoff_seconds * attempt
                LOGGER.warning(
                    "Spotify request failed on attempt %s/%s for %s: %s. Retrying in %.1fs.",
                    attempt,
                    attempts,
                    url,
                    exc,
                    sleep_for,
                )
                time.sleep(sleep_for)

        assert last_error is not None
        raise last_error


class SpotifyAuthFlow:
    AUTHORIZE_URL = "https://accounts.spotify.com/authorize"

    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.client = SpotifyClient(settings)

    def run_interactive(self, no_browser: bool = False, timeout_seconds: int = 180) -> int:
        if not self.settings.spotify_client_id or not self.settings.spotify_client_secret:
            print("SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set in .env before auth.")
            return 1

        state = self.make_state()
        authorize_url = self.build_authorize_url(state)
        host, port = self._callback_host_port()
        print(f"Open this URL and authorize the app:\n{authorize_url}\n")
        print(f"Waiting for callback on {host}:{port} for up to {timeout_seconds} seconds...")

        if not no_browser:
            webbrowser.open(authorize_url)

        result = self._wait_for_callback(state=state, host=host, port=port, timeout_seconds=timeout_seconds)
        if "error" in result:
            print(f"Spotify auth failed: {result['error']}")
            return 1

        code = result.get("code")
        if not code:
            print("Spotify auth failed: callback did not include an authorization code.")
            return 1

        return self.exchange_code(code)

    def exchange_code(self, code: str) -> int:
        if not self.settings.spotify_client_id or not self.settings.spotify_client_secret:
            print("SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set in .env before auth.")
            return 1

        response = self.client._request_with_retries(
            method="POST",
            url=SpotifyClient.TOKEN_URL,
            headers=self.client._token_headers(),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.settings.spotify_redirect_uri,
            },
        )
        response.raise_for_status()
        payload = response.json()
        tokens = self.client._normalize_token_payload(payload)
        self.client._save_cached_tokens(tokens)
        print(f"Spotify token saved to {self.settings.spotify_token_cache_path}")
        return 0

    def make_state(self, preferred: str = "") -> str:
        return preferred or secrets.token_urlsafe(24)

    def build_authorize_url(self, state: str) -> str:
        query = urlencode(
            {
                "client_id": self.settings.spotify_client_id,
                "response_type": "code",
                "redirect_uri": self.settings.spotify_redirect_uri,
                "scope": self.settings.spotify_scope,
                "state": state,
                "show_dialog": "true",
            }
        )
        return f"{self.AUTHORIZE_URL}?{query}"

    def _callback_host_port(self) -> tuple[str, int]:
        parsed = urlparse(self.settings.spotify_redirect_uri)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        return host, port

    def _wait_for_callback(self, state: str, host: str, port: int, timeout_seconds: int) -> dict[str, str]:
        event = Event()
        result: dict[str, str] = {}

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)
                if parsed.path != urlparse(self.server.redirect_path).path:
                    self.send_response(404)
                    self.end_headers()
                    return

                if params.get("state", [""])[0] != state:
                    result["error"] = "state_mismatch"
                    self._respond("State mismatch. You can close this tab.")
                    event.set()
                    return

                if "error" in params:
                    result["error"] = params["error"][0]
                if "code" in params:
                    result["code"] = params["code"][0]
                self._respond("Spotify auth received. You can close this tab and return to the terminal.")
                event.set()

            def log_message(self, format: str, *args: object) -> None:
                return

            def _respond(self, message: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(message.encode("utf-8"))

        server = HTTPServer((host, port), CallbackHandler)
        server.redirect_path = self.settings.spotify_redirect_uri  # type: ignore[attr-defined]
        server.timeout = 0.5
        started_at = datetime.now(UTC)

        try:
            while (datetime.now(UTC) - started_at).total_seconds() < timeout_seconds:
                server.handle_request()
                if event.is_set():
                    break
        finally:
            server.server_close()

        if not event.is_set():
            return {"error": "timeout_waiting_for_callback"}
        return result
