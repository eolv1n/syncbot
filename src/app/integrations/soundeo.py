from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from app.config import AppSettings
from app.matching.normalizer import build_normalized_track_key, extract_remix, normalize_text
from app.models import ActionType, MatchResult, NormalizedTrack, SoundeoCandidate, TrackStatus

LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page, Playwright


SEARCH_PAREN_VARIANT_PATTERN = re.compile(
    r"\(([^)]*\b(mix|edit|extended|remix|version|vip|rework)\b[^)]*)\)",
    re.IGNORECASE,
)
SEARCH_SUFFIX_VARIANT_PATTERN = re.compile(
    r"\s[-–]\s([^()]*\b(mix|edit|extended|remix|version|vip|rework)\b[^()]*)$",
    re.IGNORECASE,
)
TRAILING_SEARCH_MIXED_PATTERN = re.compile(r"\s[-–]\s*mixed\s*$", re.IGNORECASE)
CATALOG_CODE_PATTERN = re.compile(r"\s*\(([A-Z]{2,}\d{2,}|[A-Z]+\d+[A-Z0-9]*)\)", re.IGNORECASE)


class SoundeoAutomation:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._logged_in = False

    def refresh_downloaded_cache(self) -> list[SoundeoCandidate]:
        page = self._ensure_page()
        if page is None:
            LOGGER.info("Downloaded cache refresh is scaffolded and requires Playwright credentials.")
            return []

        self._ensure_logged_in(page)
        LOGGER.info("Refreshing Soundeo downloads cache from %s", self.settings.soundeo_downloads_url)
        return self._extract_paginated_candidates(
            page=page,
            start_url=self.settings.soundeo_downloads_url,
            mark_downloaded=True,
        )

    def refresh_recent_downloaded_cache(self) -> list[SoundeoCandidate]:
        page = self._ensure_page()
        if page is None:
            LOGGER.info("Downloaded cache preflight is scaffolded and requires Playwright credentials.")
            return []

        self._ensure_logged_in(page)
        LOGGER.info("Refreshing first Soundeo downloads page from %s", self.settings.soundeo_downloads_url)
        page.goto(self.settings.soundeo_downloads_url, wait_until="domcontentloaded")
        self._wait_after_action()
        return self._extract_candidates(page, mark_downloaded=True, max_results=None)

    def search_track(self, normalized: NormalizedTrack) -> list[SoundeoCandidate]:
        LOGGER.info("Searching Soundeo for '%s'", normalized.normalized_query)
        page = self._ensure_page()
        if page is None:
            self._wait_after_action()
            return []

        queries = self._search_queries(normalized)

        collected: list[SoundeoCandidate] = []
        seen_ids: set[str] = set()
        for attempt in range(2):
            self._ensure_logged_in(page)
            try:
                for index, query in enumerate(queries):
                    page.goto(
                        self._search_url(query),
                        wait_until="domcontentloaded",
                    )
                    self._wait_after_action()
                    candidates = self._extract_candidates(page, max_results=self.settings.soundeo_max_results)
                    for candidate in candidates:
                        if candidate.soundeo_track_id in seen_ids:
                            continue
                        seen_ids.add(candidate.soundeo_track_id)
                        collected.append(candidate)
                    if not candidates:
                        LOGGER.info("Soundeo search returned no candidates for fallback query '%s'.", query)
                return collected
            except Exception:
                LOGGER.warning("Soundeo search page was not ready on attempt %s for '%s'.", attempt + 1, normalized.normalized_query)
                self._logged_in = False
                if attempt == 1:
                    raise
                self._wait_after_action()
        return []

    def _search_queries(self, normalized: NormalizedTrack) -> list[str]:
        track = normalized.original
        raw_artist = track.artists_raw.strip()
        raw_title = track.title_raw.strip()
        title_queries = self._title_search_candidates(normalized, raw_title)
        artist_queries = self._artist_search_candidates(raw_artist)
        simple_query = " ".join(part for part in (normalized.artist, normalized.title) if part).strip()
        raw_query = " - ".join(part for part in (raw_artist, raw_title) if part).strip()
        compact_raw_query = self._compact_initialisms(raw_query)

        queries = [
            normalized.normalized_query,
            simple_query,
            raw_query,
            compact_raw_query,
        ]

        for title in title_queries:
            for artist in artist_queries:
                queries.append(" ".join(part for part in (artist, title) if part).strip())
                queries.append(" - ".join(part for part in (artist, title) if part).strip())

        remix = normalized.remix or ""
        remix_identity = self._variant_identity(remix)
        if remix_identity:
            queries.append(" ".join(part for part in (remix_identity, normalized.title) if part).strip())
            queries.append(" - ".join(part for part in (remix_identity, normalized.title, remix_identity) if part).strip())

        return self._dedupe_queries(queries)

    def _artist_search_candidates(self, raw_artist: str) -> list[str]:
        parts = [
            part.strip()
            for part in re.split(r"\s*,\s*|\s+feat\.?\s+|\s+ft\.?\s+", raw_artist, flags=re.IGNORECASE)
            if part.strip()
        ]
        candidates = [raw_artist, *parts]
        candidates.extend(", ".join(parts[:index]) for index in range(2, len(parts) + 1))

        for part in parts:
            subparts = [subpart.strip() for subpart in re.split(r"\s+(?:&|and)\s+", part, flags=re.IGNORECASE) if subpart.strip()]
            if len(subparts) > 1:
                candidates.extend(subparts)

        for part in parts if len(parts) == 1 else []:
            tokens = part.split()
            if len(tokens) == 2 and re.fullmatch(r"[A-Za-z][A-Za-z'’.-]{3,}", tokens[-1]):
                candidates.append(tokens[-1])

        return self._dedupe_queries(candidates)

    def _title_search_candidates(self, normalized: NormalizedTrack, raw_title: str) -> list[str]:
        base_title = self._raw_base_title(raw_title)
        stripped_catalog_title = self._strip_catalog_codes(base_title)
        normalized_without_catalog = self._strip_catalog_codes(normalized.title)
        candidates = [
            base_title,
            stripped_catalog_title,
            normalized.title,
            normalized_without_catalog,
            raw_title,
            self._strip_catalog_codes(raw_title),
        ]
        return self._dedupe_queries(candidates)

    def _raw_base_title(self, raw_title: str) -> str:
        value = TRAILING_SEARCH_MIXED_PATTERN.sub("", raw_title).strip()
        value = SEARCH_PAREN_VARIANT_PATTERN.sub("", value).strip()
        value = SEARCH_SUFFIX_VARIANT_PATTERN.sub("", value).strip()
        return re.sub(r"\s+", " ", value).strip()

    def _strip_catalog_codes(self, value: str) -> str:
        value = CATALOG_CODE_PATTERN.sub("", value).strip()
        return re.sub(r"\s+", " ", value).strip()

    def _compact_initialisms(self, value: str) -> str:
        def replace(match: re.Match[str]) -> str:
            return match.group(0).replace(".", "")

        return re.sub(r"\b(?:[A-Za-z]\.){2,}[A-Za-z]?\.?", replace, value)

    def _variant_identity(self, value: str) -> str:
        normalized = normalize_text(value)
        generic = {"mix", "edit", "extended", "remix", "version", "vip", "rework", "original", "radio", "album"}
        return " ".join(token for token in normalized.split() if token not in generic)

    def _dedupe_queries(self, queries: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for query in queries:
            compact = re.sub(r"\s+", " ", query).strip()
            if not compact:
                continue
            key = compact.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(compact)
        return result

    def apply_action(self, match: MatchResult, action_type: ActionType) -> TrackStatus:
        page = self._ensure_page()
        if page is None or match.candidate is None:
            return TrackStatus.ERROR

        if self.settings.dry_run:
            return self._status_for_action(action_type)

        self._ensure_logged_in(page)
        row = page.locator(f".trackitem[data-track-id='{match.candidate.soundeo_track_id}']").first

        if action_type == ActionType.STAR:
            if row.count() > 0 and self._is_favorited(row):
                return TrackStatus.STARRED
            if row.count() > 0 and self._try_click_locator(row.locator("button.favorites").first):
                self._wait_after_action()
                if self._is_favorited(row):
                    return TrackStatus.STARRED
            if match.candidate.url:
                page.goto(match.candidate.url, wait_until="domcontentloaded")
                if self._page_is_favorited(page):
                    return TrackStatus.STARRED
                if self._try_click(page, [".soundtrack_favorites button", "button.favorites"]):
                    self._wait_after_action()
                    if self._page_is_favorited(page):
                        return TrackStatus.STARRED
            return TrackStatus.ERROR
        if action_type == ActionType.LIKE:
            if row.count() > 0 and self._is_voted(row):
                return TrackStatus.LIKED_WAITING_AVAILABILITY
            if row.count() > 0 and self._try_click_locator(row.locator(".vote button.ico, .vote button").first):
                self._wait_after_action()
                if self._is_voted(row):
                    return TrackStatus.LIKED_WAITING_AVAILABILITY
                row_text = self._safe_inner_text(row, timeout=5_000)
                if row_text:
                    blocked = self._vote_blocked_status(row_text)
                    if blocked is not None:
                        return blocked
            if match.candidate.url:
                page.goto(match.candidate.url, wait_until="domcontentloaded")
                if self._page_is_voted(page):
                    return TrackStatus.LIKED_WAITING_AVAILABILITY
                if self._try_click(page, [".soundtrack_vote button", ".vote button.ico", ".vote button"]):
                    self._wait_after_action()
                    if self._page_is_voted(page):
                        return TrackStatus.LIKED_WAITING_AVAILABILITY
                    blocked = self._vote_blocked_status(page.content())
                    if blocked is not None:
                        return blocked
            blocked = self._vote_blocked_status(page.content())
            if blocked is not None:
                return blocked
            return TrackStatus.ERROR
        if action_type == ActionType.WAITLIST_ADD:
            return TrackStatus.NOT_FOUND_WAITLIST
        return TrackStatus.SKIPPED

    def capture_failure_artifacts(self, slug: str, html: str = "") -> tuple[Path, Path]:
        screenshot = self.settings.screenshots_dir / f"{slug}.png"
        html_path = self.settings.html_dir / f"{slug}.html"
        screenshot.write_bytes(b"")
        html_path.write_text(html, encoding="utf-8")
        return screenshot, html_path

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
        self._page = None

    def _ensure_page(self) -> Page | None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            LOGGER.info("Playwright is not installed; Soundeo automation is running in scaffold mode.")
            return None

        if self._page is not None:
            return self._page

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.settings.soundeo_headless)
        self._context = self._browser.new_context()
        self._page = self._context.new_page()
        self._page.set_default_timeout(20_000)
        self._page.set_default_navigation_timeout(20_000)
        return self._page

    def _ensure_logged_in(self, page: Page) -> None:
        if self._logged_in:
            return

        self._goto_login_entry(page)
        if self._is_logged_in(page):
            self._logged_in = True
            return
        if not self.settings.soundeo_username or not self.settings.soundeo_password:
            LOGGER.warning("Soundeo credentials are missing; search will run without authenticated actions.")
            return

        self._open_login_dialog(page)
        email_input = self._locate_login_input(page)
        password_input = self._locate_password_input(page)
        if email_input is None or password_input is None:
            LOGGER.info("Soundeo login form was not found at %s; retrying from base URL.", page.url)
            page.goto(self.settings.soundeo_base_url, wait_until="domcontentloaded")
            self._open_login_dialog(page)
            email_input = self._locate_login_input(page)
            password_input = self._locate_password_input(page)

        if email_input is None or password_input is None:
            self.capture_failure_artifacts("soundeo-login-missing-inputs", page.content())
            raise RuntimeError("Could not locate Soundeo login form inputs.")

        email_input.click()
        email_input.fill("")
        email_input.fill(self.settings.soundeo_username)
        password_input.click()
        password_input.fill("")
        password_input.fill(self.settings.soundeo_password)

        login_button = self._first_visible(
            page,
            [
                "button:has-text('Login')",
                "input[type='submit'][value='Login']",
                "text=Login",
            ],
        )
        if login_button is None:
            raise RuntimeError("Could not locate Soundeo login button.")
        login_button.click()
        self._wait_after_action()
        if not self._wait_for_logged_in(page):
            self.capture_failure_artifacts("soundeo-login-failed", page.content())
            raise RuntimeError("Soundeo login did not complete successfully.")
        self._logged_in = True

    def _extract_candidates(
        self,
        page: Page,
        mark_downloaded: bool = False,
        max_results: int | None = None,
    ) -> list[SoundeoCandidate]:
        candidates: list[SoundeoCandidate] = []
        seen_ids: set[str] = set()
        row_locator = page.locator(".folder .trackitem")
        total = row_locator.count()
        if max_results is not None:
            total = min(total, max_results)

        for index in range(total):
            row = row_locator.nth(index)
            track_id = (row.get_attribute("data-track-id") or "").strip()
            if not track_id or track_id in seen_ids:
                continue
            seen_ids.add(track_id)

            anchor = row.locator(".info strong a[href*='/track/']").first
            if anchor.count() == 0:
                continue
            title_text = anchor.inner_text().strip()
            href = anchor.get_attribute("href") or ""
            if not title_text or not href:
                continue

            url = href if href.startswith("http") else f"{self.settings.soundeo_base_url.rstrip('/')}/{href.lstrip('/')}"
            artists, title = self._split_track_link(title_text)
            row_text = row.inner_text(timeout=1_000)
            labels = [item.strip().casefold() for item in row_text.splitlines() if item.strip()]
            release_name, release_date = self._extract_release_metadata(row_text)
            has_download = row.locator(".download .track-download-lnk").count() > 0
            has_vote = row.locator(".vote").count() > 0
            candidates.append(
                SoundeoCandidate(
                    soundeo_track_id=track_id,
                    title=title,
                    artists=artists,
                    release_name=release_name,
                    release_date=release_date,
                    is_available=has_download and not has_vote,
                    is_downloaded=mark_downloaded,
                    url=url,
                    extra_labels=labels,
                )
            )

        return candidates

    def _extract_paginated_candidates(
        self,
        page: Page,
        start_url: str,
        mark_downloaded: bool = False,
    ) -> list[SoundeoCandidate]:
        page.goto(start_url, wait_until="domcontentloaded")
        self._wait_after_action()

        candidates: list[SoundeoCandidate] = []
        seen_ids: set[str] = set()
        max_page = self._max_pagination_page(page)
        LOGGER.info("Soundeo pagination discovered: %s pages starting from %s", max_page, start_url)

        for page_number in range(1, max_page + 1):
            url = start_url if page_number == 1 else f"{start_url}?page={page_number}"
            LOGGER.info("Scanning Soundeo page %s/%s: %s", page_number, max_page, url)
            page.goto(url, wait_until="domcontentloaded")
            self._wait_after_action()
            before = len(candidates)
            for candidate in self._extract_candidates(page, mark_downloaded=mark_downloaded, max_results=None):
                if candidate.soundeo_track_id in seen_ids:
                    continue
                seen_ids.add(candidate.soundeo_track_id)
                candidates.append(candidate)
            LOGGER.info(
                "Collected %s new tracks from page %s/%s (%s total)",
                len(candidates) - before,
                page_number,
                max_page,
                len(candidates),
            )

        return candidates

    def _is_logged_in(self, page: Page) -> bool:
        current_url = page.url
        if "/account/" in current_url:
            markers = [
                "text=My Downloads",
                "text=My Votes",
                "text=My Favorites",
                "text=Unvote",
            ]
            for marker in markers:
                try:
                    if page.locator(marker).count() > 0:
                        return True
                except Exception:
                    continue

        login_heading = page.locator("text=LOGIN TO YOUR ACCOUNT")
        if login_heading.count() > 0 and login_heading.first.is_visible():
            return False

        try:
            account_link = page.locator("#top-menu-account a").first
            if account_link.count() > 0:
                href = account_link.get_attribute("href") or ""
                if href and "/account/logoreg" not in href:
                    return True
        except Exception:
            pass

        try:
            if page.locator(".toast-success").filter(has_text="Welcome").count() > 0:
                return True
        except Exception:
            pass

        logout_markers = [
            "a[href*='logout']",
            "a .fa-power-off",
            "i.fa-power-off",
            "text=Logout",
        ]
        for marker in logout_markers:
            try:
                if page.locator(marker).count() > 0:
                    return True
            except Exception:
                continue

        logged_in_markers = [
            "a[href*='/account/favorites']",
            "text=My Favorites",
            "a[href*='/account/votes']",
            "text=My Votes",
        ]
        for marker in logged_in_markers:
            try:
                if page.locator(marker).count() > 0:
                    return True
            except Exception:
                continue
        return False

    def _wait_for_logged_in(self, page: Page) -> bool:
        deadline = time.time() + max(5.0, self.settings.rate_limit_seconds * 3)
        while time.time() < deadline:
            if self._is_logged_in(page):
                return True
            time.sleep(0.25)
        return self._is_logged_in(page)

    def _is_not_found_page(self, page: Page) -> bool:
        try:
            title = page.title()
        except Exception:
            title = ""
        if "404" in title:
            return True
        try:
            return page.locator("text=Error 404 - Page Not Found").count() > 0
        except Exception:
            return False

    def _wait_after_action(self) -> None:
        time.sleep(self.settings.rate_limit_seconds)

    def _first_visible(self, page: Page, selectors: list[str]):
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                if locator.count() > 0 and locator.is_visible():
                    return locator
            except Exception:
                continue
        return None

    def _try_click(self, page: Page, selectors: list[str]) -> bool:
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                if locator.count() > 0 and locator.is_visible():
                    locator.click()
                    return True
            except Exception:
                continue
        return False

    def _try_click_locator(self, locator) -> bool:
        try:
            if locator.count() > 0 and locator.is_visible():
                locator.click()
                return True
        except Exception:
            return False
        return False

    def _safe_inner_text(self, locator, timeout: int = 1_000) -> str:
        try:
            return locator.inner_text(timeout=timeout)
        except Exception:
            return ""

    def _split_track_link(self, value: str) -> tuple[str, str]:
        if " - " not in value:
            return "", value.strip()
        artists, title = value.split(" - ", 1)
        return artists.strip(), title.strip()

    def _search_url(self, query: str) -> str:
        base_url = self.settings.soundeo_search_url.rstrip("/")
        if base_url.endswith("/search"):
            return f"{base_url}?{urlencode({'q': query})}"
        return f"{self.settings.soundeo_base_url.rstrip('/')}/search?{urlencode({'q': query})}"

    def _extract_release_metadata(self, row_text: str) -> tuple[str | None, str | None]:
        compact = " ".join(part.strip() for part in row_text.splitlines() if part.strip())
        match = re.search(r"\bby\s+(?P<release>.+?)\s+at\s+(?P<date>\d{4}-\d{2}-\d{2})\b", compact)
        if not match:
            return None, None
        return match.group("release").strip(), match.group("date")

    def _soundeo_track_id(self, url: str, fallback: str) -> str:
        tail = url.rstrip("/").rsplit("/", 1)[-1]
        return tail or fallback

    def _status_for_action(self, action_type: ActionType) -> TrackStatus:
        if action_type == ActionType.STAR:
            return TrackStatus.STARRED
        if action_type == ActionType.LIKE:
            return TrackStatus.LIKED_WAITING_AVAILABILITY
        if action_type == ActionType.WAITLIST_ADD:
            return TrackStatus.NOT_FOUND_WAITLIST
        return TrackStatus.SKIPPED

    def _is_favorited(self, row) -> bool:
        try:
            classes = row.locator("button.favorites").first.get_attribute("class") or ""
            if "favored" in classes.split():
                return True
        except Exception:
            pass
        try:
            return row.locator("button.favorites.favored").count() > 0
        except Exception:
            return False

    def _page_is_favorited(self, page: Page) -> bool:
        try:
            classes = page.locator(".soundtrack_favorites button").first.get_attribute("class") or ""
            if "favored" in classes.split():
                return True
        except Exception:
            pass
        try:
            return page.locator(".soundtrack_favorites button.favored").count() > 0
        except Exception:
            return False

    def _is_voted(self, row) -> bool:
        vote_container = row.locator(".vote").first
        try:
            if vote_container.count() > 0 and "unvote" in vote_container.inner_text().casefold():
                return True
        except Exception:
            pass
        try:
            classes = vote_container.locator("button").first.get_attribute("class") or ""
            return "voted" in classes.split()
        except Exception:
            return False

    def _page_is_voted(self, page: Page) -> bool:
        vote_container = page.locator(".soundtrack_vote").first
        try:
            if vote_container.count() > 0 and "unvote" in vote_container.inner_text().casefold():
                return True
        except Exception:
            pass
        try:
            classes = vote_container.locator("button").first.get_attribute("class") or ""
            return "voted" in classes.split()
        except Exception:
            return False

    def _vote_blocked_status(self, text: str) -> TrackStatus | None:
        normalized = text.casefold()
        if "premium" in normalized and any(token in normalized for token in ("vote", "voting", "available")):
            return TrackStatus.PREMIUM_REQUIRED
        if any(phrase in normalized for phrase in ("3 votes per day", "3 vote per day", "daily vote limit", "limit reached")):
            return TrackStatus.LIKE_LIMIT_REACHED
        return None

    def to_download_cache_rows(self, candidates: list[SoundeoCandidate]) -> list[tuple[str, str, str | None]]:
        rows: list[tuple[str, str, str | None]] = []
        for candidate in candidates:
            remix = extract_remix(candidate.title)
            normalized_key = build_normalized_track_key(candidate.artists, candidate.title, remix)
            rows.append((candidate.soundeo_track_id, normalized_key, None))
        return rows

    def _open_login_dialog(self, page: Page) -> None:
        login_dialog = page.locator("text=LOGIN TO YOUR ACCOUNT")
        if login_dialog.count() > 0 and login_dialog.first.is_visible():
            return

        login_toggle = self._first_visible(
            page,
            [
                "#top-menu-account a",
                "text=Account",
                "a:has-text('Account')",
                "text=Login",
            ],
        )
        if login_toggle is not None:
            login_toggle.click()
            try:
                page.wait_for_load_state("domcontentloaded", timeout=3_000)
            except Exception:
                pass
            self._wait_after_action()

    def _goto_login_entry(self, page: Page) -> None:
        page.goto(self.settings.soundeo_login_url, wait_until="domcontentloaded")
        if self._is_not_found_page(page):
            page.goto(self.settings.soundeo_base_url, wait_until="domcontentloaded")

    def _locate_login_input(self, page: Page):
        selectors = [
            "#UserLogin",
            "input[name='data[User][login]']",
            "input.username",
            "input[type='email']",
            "input[name='email']",
            "input[placeholder*='mail' i]",
            "div:has-text('LOGIN TO YOUR ACCOUNT') input[type='text']",
            "div:has-text('LOGIN TO YOUR ACCOUNT') input:not([type='password'])",
            "input[type='text']",
        ]
        return self._first_visible(page, selectors)

    def _locate_password_input(self, page: Page):
        selectors = [
            "#UserPassword",
            "input[name='data[User][password]']",
            "input.password",
            "input[type='password']",
            "input[name='password']",
        ]
        return self._first_visible(page, selectors)

    def _max_pagination_page(self, page: Page) -> int:
        pagination_links = page.locator(".pagination a[href*='page=']")
        max_page = 1
        for index in range(pagination_links.count()):
            href = pagination_links.nth(index).get_attribute("href") or ""
            match = re.search(r"[?&]page=(\d+)", href)
            if match:
                max_page = max(max_page, int(match.group(1)))
        return max_page
