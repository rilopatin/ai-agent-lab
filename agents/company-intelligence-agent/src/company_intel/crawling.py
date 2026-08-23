from __future__ import annotations

import hashlib
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


RELEVANT_HINTS = (
    "about", "company", "team", "leadership", "founder", "technology",
    "product", "solution", "news", "press", "blog", "investor", "funding",
)
SKIP_SUFFIXES = (
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".zip",
    ".mp4", ".mp3", ".pdf", ".woff", ".woff2", ".css", ".js",
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
    ) -> None:
        self.fetcher = fetcher
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.request_delay = max(request_delay, 0.0)

    def crawl(self, company: CompanyRecord) -> CompanyCrawl:
        if not company.official_url:
            return CompanyCrawl(company.name, "", "missing_url", [], [])
        start_url = normalize_url(company.official_url)
        allowed_host = registrable_host(start_url)
        robots = self._robots(start_url)
        queue: deque[tuple[str, int]] = deque([(start_url, 0)])
        seen: set[str] = set()
        seen_content: set[str] = set()
        pages: list[CrawledPage] = []
        errors: list[str] = []
        request_count = 0
        while queue and len(pages) < self.max_pages:
            url, depth = queue.popleft()
            if url in seen:
                continue
            seen.add(url)
            if robots and not robots.can_fetch("CompanyIntelligenceAgent/0.2", url):
                errors.append(f"blocked_by_robots: {url}")
                continue
            try:
                if request_count:
                    time.sleep(self.request_delay)
                request_count += 1
                html = self.fetcher.get_text(url)
            except FetchError as exc:
                errors.append(f"fetch_failed: {url}: {exc}")
                if "HTTP Error 429" in str(exc):
                    errors.append("crawl_stopped: site rate limit reached")
                    break
                continue
            parser = PageParser()
            parser.feed(html)
            text = " ".join(parser.text_parts)
            title = " ".join(parser.title_parts)
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if content_hash in seen_content:
                continue
            seen_content.add(content_hash)
            pages.append(
                CrawledPage(
                    url=url,
                    title=title,
                    text=text,
                    depth=depth,
                    content_hash=content_hash,
                )
            )
            if depth >= self.max_depth:
                continue
            candidates: list[str] = []
            for href in parser.links:
                candidate = normalize_url(urljoin(url, href))
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
        status = "partial" if pages and errors else "ok" if pages else "failed"
        return CompanyCrawl(company.name, start_url, status, pages, errors)

    @staticmethod
    def _priority(url: str) -> tuple[int, int, str]:
        lowered = url.lower()
        relevant = any(hint in lowered for hint in RELEVANT_HINTS)
        return (0 if relevant else 1, len(urlsplit(url).path), lowered)

    @staticmethod
    def _robots(start_url: str) -> RobotFileParser | None:
        parts = urlsplit(start_url)
        robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
        parser = RobotFileParser(robots_url)
        try:
            parser.read()
        except OSError:
            return None
        return parser


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
