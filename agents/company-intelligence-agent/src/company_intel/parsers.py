from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .identity import FORMERLY_RE, clean_text, normalize_url, split_name_and_aliases
from .models import CompanyRecord


class _PortfolioHTMLParser(HTMLParser):
    """Collect h4 portfolio entries and the first external link following each."""

    def __init__(self, portfolio_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.portfolio_url = portfolio_url
        self.portfolio_host = urlsplit(portfolio_url).netloc.removeprefix("www.")
        self.entries: list[dict[str, object]] = []
        self._heading_depth = 0
        self._heading_text: list[str] = []
        self._current: dict[str, object] | None = None
        self._description_parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag in {"script", "style", "nav", "footer"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag == "h4":
            self._finish_current()
            self._heading_depth = 1
            self._heading_text = []
            return
        if self._heading_depth:
            self._heading_depth += 1
        if tag == "a" and self._current is not None and not self._current.get("official_url"):
            href = attrs_dict.get("href")
            if href:
                absolute = urljoin(self.portfolio_url, href)
                host = urlsplit(absolute).netloc.removeprefix("www.")
                if host and host != self.portfolio_host and not host.endswith("linkedin.com"):
                    self._current["official_url"] = normalize_url(absolute)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "nav", "footer"} and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
        if self._heading_depth:
            self._heading_depth -= 1
            if tag == "h4" and self._heading_depth == 0:
                raw_name = clean_text("".join(self._heading_text))
                if raw_name:
                    name, aliases = split_name_and_aliases(raw_name)
                    self._current = {"name": name, "aliases": aliases, "official_url": None}
                    self._description_parts = []

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if self._heading_depth:
            self._heading_text.append(data)
        elif self._current is not None:
            cleaned = clean_text(data)
            if cleaned and cleaned.casefold() not in {"page load link", "go to top"}:
                self._description_parts.append(cleaned)

    def close(self) -> None:
        super().close()
        self._finish_current()

    def _finish_current(self) -> None:
        if self._current is None:
            return
        description = clean_text(" ".join(self._description_parts))
        self._current["description"] = description
        self.entries.append(self._current)
        self._current = None
        self._description_parts = []


def parse_genius_ny_portfolio(html: str, portfolio_url: str) -> list[CompanyRecord]:
    parser = _PortfolioHTMLParser(portfolio_url)
    parser.feed(html)
    parser.close()

    companies: list[CompanyRecord] = []
    seen: set[tuple[str, str | None]] = set()
    for entry in parser.entries:
        name = str(entry["name"])
        if len(name) > 100:
            continue
        url = entry.get("official_url")
        description = clean_text(str(entry.get("description") or ""))
        aliases = list(entry.get("aliases") or ())
        if description:
            for match in FORMERLY_RE.findall(description):
                aliases.extend(alias.strip() for alias in match.split(",") if alias.strip())
            description = clean_text(FORMERLY_RE.sub("", description))
        key = (name.casefold(), str(url) if url else None)
        if key in seen:
            continue
        seen.add(key)
        companies.append(
            CompanyRecord(
                name=name,
                portfolio_url=portfolio_url,
                official_url=str(url) if url else None,
                description=description,
                aliases=tuple(dict.fromkeys(aliases)),
            )
        )
    return companies
