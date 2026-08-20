from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

FORMERLY_RE = re.compile(r"\(\s*formerly\s+([^\)]+)\)", re.IGNORECASE)


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or None


def split_name_and_aliases(value: str) -> tuple[str, tuple[str, ...]]:
    cleaned = clean_text(value) or ""
    aliases = tuple(
        alias.strip()
        for match in FORMERLY_RE.findall(cleaned)
        for alias in re.split(r"\s*(?:,|/|\band\b)\s*", match)
        if alias.strip()
    )
    current_name = FORMERLY_RE.sub("", cleaned).strip(" -–—")
    return current_name, aliases


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", ascii_value.casefold())


def normalize_url(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    parts = urlsplit(value)
    host = parts.netloc.casefold()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((parts.scheme.casefold(), host, path, "", ""))


def domain_key(value: str | None) -> str | None:
    normalized = normalize_url(value)
    return urlsplit(normalized).netloc if normalized else None


def company_key(name: str, official_url: str | None) -> str:
    domain = domain_key(official_url)
    return f"domain:{domain}" if domain else f"name:{normalize_name(name)}"

