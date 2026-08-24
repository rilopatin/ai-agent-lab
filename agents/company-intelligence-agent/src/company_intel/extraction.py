from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


CATEGORY_TERMS = {
    "leadership": (
        "founder", "co-founder", "cofounder", "chief executive", "ceo",
        "leadership", "management team",
    ),
    "products": (
        "product", "platform", "solution", "system", "software", "hardware",
    ),
    "technology": (
        "technology", "autonomous", "autonomy", "computer vision", "lidar",
        "radar", "sensor", "camera", "artificial intelligence", "machine learning",
    ),
    "applications": (
        "use case", "mission", "defense", "agriculture", "inspection", "mapping",
        "surveillance", "logistics", "public safety", "search and rescue",
    ),
    "funding": (
        "funding round", "funded by", "secured investment", "backed by",
        "seed round", "series a", "series b", "series c", "raised",
    ),
    "locations": (
        "headquarters", "headquartered", "based in",
    ),
    "news": (
        "press release", "announced", "partnership", "award", "contract",
    ),
}

NON_CONTENT_STATUSES = {
    "no_url_provided", "site_unavailable", "placeholder_no_content",
    "site_not_found", "site_error",
}
SKIP_EVIDENCE_PATH_MARKERS = (
    "/privacy", "/terms", "/cookie", "/legal", "/accessibility",
)
NAVIGATION_MARKERS = (
    "menu close", "cookie preferences", "all rights reserved", "privacy policy",
    "top of page", "click here home products",
)

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def latest_crawl(export_dir: str | Path) -> Path:
    candidates = sorted(Path(export_dir).glob("company_sites_*.json"))
    if not candidates:
        raise FileNotFoundError("no company_sites_*.json export found; run crawl first")
    return candidates[-1]


def _sentences(text: str) -> list[str]:
    normalized = " ".join(text.split())
    return [item.strip() for item in re.split(r"(?<=[.!?])\s+", normalized) if item.strip()]


def _usable_pages(company: dict) -> list[dict]:
    if company.get("status") in NON_CONTENT_STATUSES:
        return []
    return [
        page for page in company.get("pages", [])
        if not any(marker in page.get("url", "").casefold() for marker in SKIP_EVIDENCE_PATH_MARKERS)
    ]


def _quality_sentence(sentence: str) -> bool:
    lowered = sentence.casefold()
    if len(sentence) < 20:
        return False
    if any(marker in lowered for marker in NAVIGATION_MARKERS):
        return False
    return True


def _valid_category_match(category: str, sentence: str, matched: list[str]) -> bool:
    lowered = sentence.casefold()
    if category == "funding":
        return bool(re.search(
            r"\b(raised|raises|funding round|funded by|secured (?:an? )?investment|"
            r"backed by|seed round|series [abc](?: round)?)\b",
            lowered,
        ))
    return bool(matched)


def _evidence(
    pages: list[dict], category: str, terms: tuple[str, ...], maximum: int = 5
) -> list[dict]:
    candidates: list[tuple[int, dict]] = []
    seen: set[str] = set()
    for page in pages:
        for sentence in _sentences(page.get("text", "")):
            if not _quality_sentence(sentence):
                continue
            lowered = sentence.casefold()
            matched = [term for term in terms if term in lowered]
            if not _valid_category_match(category, sentence, matched):
                continue
            snippet = sentence[:500]
            key = snippet.casefold()
            if key in seen:
                continue
            seen.add(key)
            item = {
                "source_url": page.get("url", ""),
                "source_title": page.get("title", ""),
                "matched_terms": matched,
                "snippet": snippet,
            }
            source_hint = f"{page.get('url', '')} {page.get('title', '')}".casefold()
            score = len(matched) * 10
            score += 4 if any(term in source_hint for term in terms) else 0
            score += 2 if 60 <= len(sentence) <= 350 else 0
            candidates.append((score, item))
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in candidates[:maximum]]


def extract_company(company: dict) -> dict:
    pages = _usable_pages(company)
    contacts = sorted({
        email.lower()
        for page in pages
        for email in EMAIL_RE.findall(page.get("text", ""))
    })
    evidence = {
        category: _evidence(pages, category, terms)
        for category, terms in CATEGORY_TERMS.items()
    }
    return {
        "company": company.get("company", ""),
        "website": company.get("start_url", ""),
        "crawl_status": company.get("status", ""),
        "extraction_status": "evidence_ready" if pages else "no_content_available",
        "source_page_count": len(pages),
        "contacts": {"emails": contacts},
        "evidence": evidence,
    }


def extract_crawl_file(input_path: str | Path) -> dict:
    source = Path(input_path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    profiles = [extract_company(company) for company in payload.get("companies", [])]
    return {
        "source_file": str(source),
        "company_count": len(profiles),
        "evidence_ready": sum(p["extraction_status"] == "evidence_ready" for p in profiles),
        "no_content_available": sum(
            p["extraction_status"] == "no_content_available" for p in profiles
        ),
        "profiles": profiles,
    }


def export_extraction(payload: dict, export_dir: str | Path) -> Path:
    directory = Path(export_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"company_evidence_{utc_stamp()}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
