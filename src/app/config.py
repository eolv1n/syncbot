from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None, default: int, min_value: int | None = None, max_value: int | None = None) -> int:
    if value is None or value == "":
        result = default
    else:
        result = int(value)
    if min_value is not None:
        result = max(min_value, result)
    if max_value is not None:
        result = min(max_value, result)
    return result


def _as_float(value: str | None, default: float) -> float:
    return default if value is None or value == "" else float(value)


def _as_path(value: str | None, default: Path) -> Path:
    return Path(value).expanduser() if value else default


class AppSettings:
    def __init__(self) -> None:
        _load_env_file(PROJECT_ROOT / ".env")

        self.app_env = os.getenv("APP_ENV", "development")
        self.log_level = os.getenv("LOG_LEVEL", "INFO")
        self.dry_run = _as_bool(os.getenv("DRY_RUN"), False)
        self.rate_limit_seconds = _as_float(os.getenv("RATE_LIMIT_SECONDS"), 2.0)
        self.waitlist_retry_days = _as_int(os.getenv("WAITLIST_RETRY_DAYS"), 1, min_value=1)
        self.soundeo_max_results = _as_int(os.getenv("SOUNDEO_MAX_RESULTS"), 10, min_value=1)
        self.soundeo_headless = _as_bool(os.getenv("SOUNDEO_HEADLESS"), True)

        self.base_dir = _as_path(os.getenv("BASE_DIR"), PROJECT_ROOT)
        self.data_dir = _as_path(os.getenv("DATA_DIR"), self.base_dir / "data")
        self.logs_dir = _as_path(os.getenv("LOGS_DIR"), self.base_dir / "logs")
        self.artifacts_dir = _as_path(os.getenv("ARTIFACTS_DIR"), self.base_dir / "artifacts")
        self.screenshots_dir = _as_path(
            os.getenv("SCREENSHOTS_DIR"),
            self.artifacts_dir / "screenshots",
        )
        self.html_dir = _as_path(os.getenv("HTML_DIR"), self.artifacts_dir / "html")
        self.reports_dir = _as_path(os.getenv("REPORTS_DIR"), self.artifacts_dir / "reports")
        self.playwright_state_dir = _as_path(
            os.getenv("PLAYWRIGHT_STATE_DIR"),
            self.base_dir / "playwright" / ".auth",
        )
        self.sqlite_path = _as_path(os.getenv("SQLITE_PATH"), self.data_dir / "app.db")

        self.spotify_client_id = os.getenv("SPOTIFY_CLIENT_ID")
        self.spotify_client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
        self.spotify_refresh_token = os.getenv("SPOTIFY_REFRESH_TOKEN")
        self.spotify_access_token = os.getenv("SPOTIFY_ACCESS_TOKEN")
        self.spotify_redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8899/callback")
        self.spotify_page_size = _as_int(os.getenv("SPOTIFY_PAGE_SIZE"), 50, min_value=1, max_value=50)

        self.soundeo_base_url = os.getenv("SOUNDEO_BASE_URL", "https://soundeo.com")
        self.soundeo_login_url = os.getenv("SOUNDEO_LOGIN_URL", "https://soundeo.com/login")
        self.soundeo_search_url = os.getenv("SOUNDEO_SEARCH_URL", "https://soundeo.com/search")
        self.soundeo_downloads_url = os.getenv("SOUNDEO_DOWNLOADS_URL", "https://soundeo.com/downloaded")
        self.soundeo_username = os.getenv("SOUNDEO_USERNAME")
        self.soundeo_password = os.getenv("SOUNDEO_PASSWORD")

        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

        self.base_dir = self.base_dir.resolve()
        self.data_dir = self.data_dir.resolve()
        self.logs_dir = self.logs_dir.resolve()
        self.artifacts_dir = self.artifacts_dir.resolve()
        self.screenshots_dir = self.screenshots_dir.resolve()
        self.html_dir = self.html_dir.resolve()
        self.reports_dir = self.reports_dir.resolve()
        self.playwright_state_dir = self.playwright_state_dir.resolve()
        self.sqlite_path = self.sqlite_path.resolve()

    def model_dump(self) -> dict[str, object]:
        return self.__dict__.copy()


@lru_cache(maxsize=1)
def load_settings() -> AppSettings:
    return AppSettings()
