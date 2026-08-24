from __future__ import annotations

import hashlib
import csv
import json
import re
import time
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from .fetch import FetchError, PageFetcher
from .models import CompanyRecord
from .robots_audit import ROBOT_TOKEN, RobotsInspection, RobotsInspector


RELEVANT_HINTS = (
    "about", "company", "team", "leadership", "founder", "technology",
    "product", "solution", "news", "press", "blog", "investor", "funding",
)
SKIP_SUFFIXES = (
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".zip",
    ".mp4", ".mp3", ".pdf", ".woff", ".woff2", ".css", ".js",
)
SKIP_PATH_MARKERS = (
    "/cdn-cgi/", "/imunify-bot-check",
)

CHALLENGE_MARKERS = (
    ("cloudflare", ("cf-chl-", "cf-browser-verification", "attention required! | cloudflare")),
    ("generic", ("checking your browser", "verify you are human", "enable javascript and cookies to continue")),
)


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def normalize_url(url: str) -> str:
    clean, _ = urldefrag(url)
    parts = urlsplit(clean)
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def registrable_host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self._in_title = False
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        value = " ".join(data.split())
        if not value:
            return
        self.text_parts.append(value)
        if self._in_title:
            self.title_parts.append(value)


@dataclass(slots=True)
class CrawledPage:
    url: str
    title: str
    text: str
    depth: int
    content_hash: str


@dataclass(slots=True)
class CompanyCrawl:
    company: str
    start_url: str
    status: str
    pages: list[CrawledPage]
    errors: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "company": self.company,
            "start_url": self.start_url,
            "status": self.status,
            "page_count": len(self.pages),
            "pages": [asdict(page) for page in self.pages],
            "errors": self.errors,
        }


class CompanySiteCrawler:
    def __init__(
        self,
        fetcher: PageFetcher,
        max_pages: int = 15,
        max_depth: int = 2,
        request_delay: float = 1.0,
        rate_limit_retries: int = 2,
        rate_limit_backoff: float = 15.0,
    ) -> None:
        self.fetcher = fetcher
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.request_delay = max(request_delay, 0.0)
        self.rate_limit_retries = max(rate_limit_retries, 0)
        self.rate_limit_backoff = max(rate_limit_backoff, 0.0)

    def crawl(self, company: CompanyRecord) -> CompanyCrawl:
        if not company.official_url:
            return CompanyCrawl(company.name, "", "no_url_provided", [], [])
        start_url = normalize_url(company.official_url)
        allowed_host = registrable_host(start_url)
        robots = self._robots(company)
        robots_parser: RobotFileParser | None = None
        effective_delay = self.request_delay
        if robots:
            if robots.state == "robots_temporarily_unreachable":
                return CompanyCrawl(
                    company.name, start_url, "site_unavailable", [],
                    [f"robots_unreachable: {robots.robots_url}: {robots.error}"],
                )
            effective_delay = max(effective_delay, robots.crawl_delay)
            if robots.content:
                robots_parser = RobotFileParser()
                robots_parser.set_url(robots.final_url)
                robots_parser.parse(robots.content.splitlines())
        queue: deque[tuple[str, int]] = deque([(start_url, 0)])
        seen: set[str] = set()
        seen_content: set[str] = set()
        pages: list[CrawledPage] = []
        errors: list[str] = []
        request_count = 0
        browser_mode = False
        terminal_state: str | None = None
        while queue and len(pages) < self.max_pages:
            url, depth = queue.popleft()
            if url in seen:
                continue
            seen.add(url)
            if robots_parser and not robots_parser.can_fetch(ROBOT_TOKEN, url):
                errors.append(f"robots_restricted: {url}")
                continue
            try:
                if request_count:
                    time.sleep(effective_delay)
                request_count += 1
                if browser_mode:
                    html, final_url = self._fetch_browser(url)
                else:
                    html, browser_mode, final_url = self._fetch_with_rate_limit_retry(url, errors)
            except FetchError as exc:
                if depth == 0 and "HTTP Error 4" in str(exc) or depth == 0 and "HTTP Error 5" in str(exc):
                    try:
                        page = self.fetcher.get_page_browser(url, allow_http_errors=True)
                        html = page.text
                        final_url = page.final_url
                        browser_mode = True
                        errors.append(f"http_error_page_rendered: {url}: {exc}")
                    except (AttributeError, FetchError) as browser_exc:
                        errors.append(f"fetch_failed: {url}: {exc}")
                        errors.append(f"browser_error_page_failed: {url}: {browser_exc}")
                        continue
                else:
                    errors.append(f"fetch_failed: {url}: {exc}")
                    if "HTTP Error 429" in str(exc):
                        errors.append("crawl_stopped: automation rate limit reached")
                        break
                    continue
            page_url = normalize_url(final_url)
            if depth == 0 and registrable_host(page_url) != allowed_host:
                allowed_host = registrable_host(page_url)
                redirected = CompanyRecord(
                    company.name, company.portfolio_url, page_url,
                    company.description, company.aliases,
                )
                redirected_robots = self._robots(redirected)
                if redirected_robots:
                    effective_delay = max(effective_delay, redirected_robots.crawl_delay)
                    if redirected_robots.content:
                        robots_parser = RobotFileParser()
                        robots_parser.set_url(redirected_robots.final_url)
                        robots_parser.parse(redirected_robots.content.splitlines())
                    elif redirected_robots.state == "robots_temporarily_unreachable":
                        errors.append(
                            f"redirected_robots_unreachable: {redirected_robots.robots_url}"
                        )
                        robots_parser = None
            challenge = self._challenge_provider(html)
            if challenge:
                errors.append(f"anti_bot_challenge: {challenge}: {url}")
                break
            parser = PageParser()
            parser.feed(html)
            text = " ".join(parser.text_parts)
            title = " ".join(parser.title_parts)
            if depth == 0:
                terminal_state = self._content_state(title, text)
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if content_hash in seen_content:
                continue
            seen_content.add(content_hash)
            pages.append(
                CrawledPage(
                    url=page_url,
                    title=title,
                    text=text,
                    depth=depth,
                    content_hash=content_hash,
                )
            )
            if terminal_state:
                break
            if depth >= self.max_depth:
                continue
            candidates: list[str] = []
            for href in parser.links:
                lowered_href = href.strip().lower()
                if (
                    lowered_href.startswith(("mailto:", "tel:", "javascript:"))
                    or "@" in lowered_href
                    or any(marker in lowered_href for marker in SKIP_PATH_MARKERS)
                ):
                    continue
                candidate = normalize_url(urljoin(page_url, href))
                parts = urlsplit(candidate)
                if parts.scheme not in {"http", "https"}:
                    continue
                if registrable_host(candidate) != allowed_host:
                    continue
                if parts.path.lower().endswith(SKIP_SUFFIXES):
                    continue
                if candidate not in seen:
                    candidates.append(candidate)
            candidates.sort(key=self._priority)
            queue.extend((candidate, depth + 1) for candidate in candidates)
        rate_limited = any("automation rate limit reached" in item for item in errors)
        crawl_interrupted = rate_limited or any(
            item.startswith("anti_bot_challenge:") for item in errors
        )
        browser_succeeded = any(item.startswith("browser_fallback_succeeded:") for item in errors)
        meaningful_errors = [
            item for item in errors
            if not item.startswith(("browser_fallback_succeeded:", "rate_limit_retry:"))
        ]
        status = (
            terminal_state if terminal_state
            else "ok_browser_fallback" if pages and browser_succeeded and not meaningful_errors
            else "partial" if pages and crawl_interrupted
            else "ok_with_warnings" if pages and meaningful_errors
            else "ok" if pages
            else "automation_rate_limited" if rate_limited
            else "robots_restricted" if any(item.startswith("robots_restricted:") for item in errors)
            else "failed"
        )
        return CompanyCrawl(company.name, start_url, status, pages, errors)

    def _fetch_with_rate_limit_retry(
        self, url: str, errors: list[str]
    ) -> tuple[str, bool, str]:
        last_error: FetchError | None = None
        for attempt in range(self.rate_limit_retries + 1):
            try:
                get_page = getattr(self.fetcher, "get_page", None)
                if get_page:
                    page = get_page(url)
                    return page.text, False, page.final_url
                return self.fetcher.get_text(url), False, url
            except FetchError as exc:
                last_error = exc
                if "HTTP Error 429" not in str(exc) or attempt >= self.rate_limit_retries:
                    break
                delay = self.rate_limit_backoff * (2 ** attempt)
                errors.append(
                    f"rate_limit_retry: {url}: attempt {attempt + 1}; wait {delay:g}s"
                )
                if delay:
                    time.sleep(delay)
        browser_fetch = getattr(self.fetcher, "get_text_browser", None)
        if last_error and "HTTP Error 429" in str(last_error) and browser_fetch:
            try:
                get_browser_page = getattr(self.fetcher, "get_page_browser", None)
                if get_browser_page:
                    page = get_browser_page(url)
                    html, final_url = page.text, page.final_url
                else:
                    html, final_url = browser_fetch(url), url
                errors.append(f"browser_fallback_succeeded: {url}")
                return html, True, final_url
            except FetchError as exc:
                errors.append(f"browser_fallback_failed: {url}: {exc}")
                last_error = exc
        if last_error:
            raise last_error
        raise RuntimeError("unreachable")

    def _fetch_browser(self, url: str) -> tuple[str, str]:
        get_browser_page = getattr(self.fetcher, "get_page_browser", None)
        if get_browser_page:
            page = get_browser_page(url)
            return page.text, page.final_url
        return self.fetcher.get_text_browser(url), url

    @staticmethod
    def _challenge_provider(html: str) -> str | None:
        lowered = html.lower()
        for provider, markers in CHALLENGE_MARKERS:
            if any(marker in lowered for marker in markers):
                return provider
        return None

    @staticmethod
    def _content_state(title: str, text: str) -> str | None:
        combined = f"{title} {text}".lower()
        if "critical error on this website" in combined:
            return "site_error"
        if "site not found" in combined or "does not have a domain assigned" in combined:
            return "site_not_found"
        if len(text) < 1000 and any(marker in combined for marker in (
            "coming soon", "under construction", "check back soon",
        )):
            return "placeholder_no_content"
        return None

    @staticmethod
    def _priority(url: str) -> tuple[int, int, str]:
        lowered = url.lower()
        relevant = any(hint in lowered for hint in RELEVANT_HINTS)
        return (0 if relevant else 1, len(urlsplit(url).path), lowered)

    @staticmethod
    def _robots(company: CompanyRecord) -> RobotsInspection | None:
        return RobotsInspector().inspect(company)


def export_crawls(crawls: list[CompanyCrawl], export_dir: str | Path) -> Path:
    directory = Path(export_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"company_sites_{utc_stamp()}.json"
    payload = {
        "company_count": len(crawls),
        "page_count": sum(len(item.pages) for item in crawls),
        "companies": [item.to_dict() for item in crawls],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def access_category(crawl: CompanyCrawl) -> str:
    errors = "\n".join(crawl.errors).lower()
    if crawl.status in {"missing_url", "no_url_provided"}:
        return "no_url_provided"
    if crawl.status in {
        "ok_browser_fallback", "ok_with_warnings", "placeholder_no_content", "site_not_found",
        "site_error", "site_unavailable", "robots_restricted",
    }:
        return crawl.status
    if "anti_bot_challenge:" in errors:
        return "anti_bot_challenge"
    if "http error 429" in errors:
        return "automation_rate_limited"
    if "blocked_by_robots:" in errors:
        return "robots_restricted"
    if "http error 401" in errors or "http error 403" in errors:
        return "access_denied"
    if crawl.pages:
        return "accessible_with_warnings" if crawl.errors else "accessible"
    return "site_unavailable"


def export_access_audit(
    crawls: list[CompanyCrawl], export_dir: str | Path
) -> tuple[Path, Path, dict[str, int]]:
    directory = Path(export_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = utc_stamp()
    json_path = directory / f"company_access_audit_{stamp}.json"
    csv_path = directory / f"company_access_audit_{stamp}.csv"
    rows = []
    counts: dict[str, int] = {}
    for crawl in crawls:
        category = access_category(crawl)
        counts[category] = counts.get(category, 0) + 1
        rows.append({
            "company": crawl.company,
            "start_url": crawl.start_url,
            "category": category,
            "processed": True,
            "direct_crawl": (
                "skipped_no_url" if category == "no_url_provided"
                else "skipped_site_unavailable" if category == "site_unavailable"
                else "attempted"
            ),
            "pages": len(crawl.pages),
            "errors": " | ".join(crawl.errors),
        })
    total = len(crawls)
    payload = {
        "company_count": total,
        "counts": counts,
        "percentages": {
            key: round(value * 100 / total, 1) if total else 0
            for key, value in counts.items()
        },
        "companies": rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys() if rows else (
            "company", "start_url", "category", "processed", "direct_crawl", "pages", "errors"
        ))
        writer.writeheader()
        writer.writerows(rows)
    return json_path, csv_path, counts
