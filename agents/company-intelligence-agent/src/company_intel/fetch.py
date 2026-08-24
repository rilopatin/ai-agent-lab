from __future__ import annotations

import gzip
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class FetchError(RuntimeError):
    pass


@dataclass(slots=True)
class FetchedPage:
    text: str
    final_url: str
    status: int


@dataclass(slots=True)
class PageFetcher:
    timeout_seconds: float = 30.0
    user_agent: str = "HyperVisionCompanyMonitor/0.1 (+https://www.hypervision.ai/)"

    def get_text(self, url: str) -> str:
        return self.get_page(url).text

    def get_page(self, url: str) -> FetchedPage:
        return self._get_page(url, {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Encoding": "gzip",
        })

    def get_text_browser(self, url: str) -> str:
        return self.get_page_browser(url).text

    def get_page_browser(
        self, url: str, allow_http_errors: bool = False
    ) -> FetchedPage:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise FetchError(
                "Browser fallback unavailable: install the 'playwright' package"
            ) from exc
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="chrome", headless=True)
                try:
                    page = browser.new_page()
                    response = page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=self.timeout_seconds * 1000,
                    )
                    if response is None:
                        raise FetchError(f"Browser returned no response for {url}")
                    if response.status >= 400 and not allow_http_errors:
                        raise FetchError(
                            f"Unable to fetch {url} in Chrome: HTTP Error {response.status}"
                        )
                    return FetchedPage(page.content(), page.url, response.status)
                finally:
                    browser.close()
        except PlaywrightError as exc:
            raise FetchError(f"Unable to fetch {url} in Chrome: {exc}") from exc

    def _get_page(self, url: str, headers: dict[str, str]) -> FetchedPage:
        request = Request(
            url,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    payload = gzip.decompress(payload)
                charset = response.headers.get_content_charset() or "utf-8"
                return FetchedPage(
                    payload.decode(charset, errors="replace"),
                    response.geturl(),
                    response.status,
                )
        except (HTTPError, URLError, TimeoutError) as exc:
            raise FetchError(f"Unable to fetch {url}: {exc}") from exc
