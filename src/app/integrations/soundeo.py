from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from app.config import AppSettings
from app.matching.normalizer import build_normalized_track_key, extract_remix
from app.models import ActionType, MatchResult, NormalizedTrack, SoundeoCandidate, TrackStatus

LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page, Playwright


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

    def search_track(self, normalized: NormalizedTrack) -> list[SoundeoCandidate]:
        LOGGER.info("Searching Soundeo for '%s'", normalized.normalized_query)
        page = self._ensure_page()
        if page is None:
            self._wait_after_action()
            return []

        self._ensure_logged_in(page)
        page.goto(self.settings.soundeo_search_url, wait_until="domcontentloaded")
        search_input = page.locator("input[placeholder='Search']").first
        search_input.wait_for(timeout=10_000)
        search_input.fill(normalized.normalized_query)
        search_input.press("Enter")
        self._wait_after_action()
        return self._extract_candidates(page, max_results=self.settings.soundeo_max_results)

    def apply_action(self, match: MatchResult, action_type: ActionType) -> TrackStatus:
        page = self._ensure_page()
        if page is None or match.candidate is None:
            return TrackStatus.ERROR

        if self.settings.dry_run:
            return self._status_for_action(action_type)

        self._ensure_logged_in(page)
        row = page.locator(f".trackitem[data-track-id='{match.candidate.soundeo_track_id}']").first

        if action_type == ActionType.STAR:
            if row.count() > 0 and self._try_click_locator(row.locator("button.favorites").first):
                self._wait_after_action()
                if self._is_favorited(row):
                    return TrackStatus.STARRED
            if match.candidate.url:
                page.goto(match.candidate.url, wait_until="domcontentloaded")
                if self._try_click(page, [".soundtrack_favorites button", "button.favorites"]):
                    self._wait_after_action()
                    if self._page_is_favorited(page):
                        return TrackStatus.STARRED
            return TrackStatus.ERROR
        if action_type == ActionType.LIKE:
            if row.count() > 0 and self._try_click_locator(row.locator(".vote button.ico, .vote button").first):
                self._wait_after_action()
                if self._is_voted(row):
                    return TrackStatus.LIKED_WAITING_AVAILABILITY
            if match.candidate.url:
                page.goto(match.candidate.url, wait_until="domcontentloaded")
                if self._try_click(page, [".soundtrack_vote button", ".vote button.ico", ".vote button"]):
                    self._wait_after_action()
                    if self._page_is_voted(page):
                        return TrackStatus.LIKED_WAITING_AVAILABILITY
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
        self._page.set_default_timeout(10_000)
        return self._page

    def _ensure_logged_in(self, page: Page) -> None:
        if self._logged_in:
            return

        page.goto(self.settings.soundeo_login_url, wait_until="domcontentloaded")
        if self._is_not_found_page(page):
            page.goto(self.settings.soundeo_base_url, wait_until="domcontentloaded")
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
            has_download = row.locator(".download .track-download-lnk").count() > 0
            has_vote = row.locator(".vote").count() > 0
            candidates.append(
                SoundeoCandidate(
                    soundeo_track_id=track_id,
                    title=title,
                    artists=artists,
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

    def _split_track_link(self, value: str) -> tuple[str, str]:
        if " - " not in value:
            return "", value.strip()
        artists, title = value.split(" - ", 1)
        return artists.strip(), title.strip()

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
            self._wait_after_action()

    def _locate_login_input(self, page: Page):
        selectors = [
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
