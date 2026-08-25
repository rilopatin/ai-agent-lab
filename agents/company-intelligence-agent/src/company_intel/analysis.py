from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


CATEGORIES = (
    "leadership", "products", "technology", "applications",
    "funding", "locations", "news",
)
LEADERSHIP_ROLE_PATTERN = re.compile(
    r"\b(?:co[- ]?founder|founder|ceo|cto|cfo|coo|chief\s+[a-z]+(?:\s+[a-z]+)?"
    r"|president|vice president|vp|svp|director|head of|board (?:chair|chairman|member|advisor))\b",
    re.IGNORECASE,
)
NEWS_EVENT_PATTERN = re.compile(
    r"\b(?:announc\w*|sign(?:ed|s)?|partnered|formed|engaged|enter(?:ed|s)?|"
    r"launch(?:ed|es|ing)?|feature(?:d|s)?|contract(?:ed)?|won|winning|recogniz\w*|"
    r"select(?:ed|s)?|award(?:ed|s)?|receiv(?:ed|es)?|participat\w*|began|beginning|"
    r"secur(?:ed|es)|collaborat\w*|rais(?:ed|es|ing)|acquir\w*|appoint\w*|"
    r"open(?:ed|s|ing)?)\b",
    re.IGNORECASE,
)
EVIDENCE_GROUPS = (
    ("leadership", "locations"),
    ("products", "technology", "applications"),
    ("funding", "news"),
)

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "facts": {
            "type": "object",
            "properties": {
                category: {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "statement": {"type": "string"},
                            "source_url": {"type": "string"},
                            "evidence_quote": {"type": "string"},
                            "confidence": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                            },
                        },
                        "required": [
                            "statement", "source_url", "evidence_quote", "confidence"
                        ],
                    },
                }
                for category in CATEGORIES
            },
            "required": list(CATEGORIES),
        },
    },
    "required": ["summary", "facts"],
}


class AnalysisError(RuntimeError):
    pass


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def latest_evidence(export_dir: str | Path) -> Path:
    candidates = sorted(Path(export_dir).glob("company_evidence_*.json"))
    if not candidates:
        raise FileNotFoundError("no company_evidence_*.json export found; run extract first")
    return candidates[-1]


def _compact_profile(
    profile: dict, categories: tuple[str, ...], maximum_per_category: int = 5
) -> dict:
    company = profile.get("company", "")

    def relevant(category: str, item: dict) -> bool:
        snippet = _normalize_text(item.get("snippet", ""))
        url = item.get("source_url", "").casefold()
        title = _normalize_text(item.get("source_title", ""))
        company_name = _normalize_text(company)
        company_is_named = bool(company_name and company_name in snippet)
        company_is_in_title = bool(company_name and company_name in title)
        if category == "leadership":
            has_role = bool(LEADERSHIP_ROLE_PATTERN.search(snippet))
            return has_role and (
                company_is_named or company_is_in_title or any(
                    marker in url
                    for marker in ("/team", "/about", "/leadership", "/people")
                )
            )
        if category == "products":
            return not any(marker in snippet for marker in (
                "how do we ", "our mission is", " exists to ",
            ))
        if category == "news":
            return company_is_named and bool(NEWS_EVENT_PATTERN.search(snippet))
        if category == "locations":
            company_positions = [
                match.start() for match in re.finditer(re.escape(company_name), snippet)
            ] if company_name else []
            location_positions = [
                match.start()
                for match in re.finditer(r"\b(?:based in|headquartered|headquarters)\b", snippet)
            ]
            return any(
                abs(company_position - location_position) <= 45
                for company_position in company_positions
                for location_position in location_positions
            )
        if category == "technology":
            return (
                company_is_named or company_is_in_title
                or snippet.startswith(("our ", "we "))
            )
        return True

    def selected(category: str) -> list[dict]:
        candidates = [
            item for item in profile.get("evidence", {}).get(category, [])
            if relevant(category, item)
        ]
        return [
            {
                "source_url": item.get("source_url", ""),
                "source_title": item.get("source_title", ""),
                "snippet": item.get("snippet", ""),
            }
            for item in candidates[:maximum_per_category]
        ]

    return {
        "company": company,
        "website": profile.get("website", ""),
        "contacts": profile.get("contacts", {}),
        "evidence": {
            category: selected(category)
            for category in categories
        },
    }


def _default_transport(endpoint: str, payload: dict, timeout: int) -> dict:
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except TimeoutError as exc:
        raise AnalysisError(
            f"local Ollama request exceeded the {timeout}-second timeout"
        ) from exc
    except urllib.error.URLError as exc:
        raise AnalysisError(
            "cannot connect to local Ollama; make sure Ollama is running"
        ) from exc


def _validate_result(result: dict, allowed_urls: set[str]) -> dict:
    if not isinstance(result.get("summary"), str) or not isinstance(result.get("facts"), dict):
        raise AnalysisError("model returned an invalid analysis structure")
    clean_facts: dict[str, list[dict]] = {}
    for category in CATEGORIES:
        items = result["facts"].get(category, [])
        if not isinstance(items, list):
            raise AnalysisError(f"model returned invalid {category} facts")
        clean: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = item.get("source_url", "")
            statement = item.get("statement", "").strip()
            confidence = item.get("confidence", "")
            quote = item.get("evidence_quote", "").strip()
            if statement and quote and url in allowed_urls and confidence in {"high", "medium", "low"}:
                clean.append({
                    "statement": statement,
                    "source_url": url,
                    "evidence_quote": quote,
                    "confidence": confidence,
                })
        clean_facts[category] = clean[:5]
    return {"summary": result["summary"].strip(), "facts": clean_facts}


def _model_request(
    compact: dict,
    model: str,
    endpoint: str,
    timeout: int,
    transport: Callable[[str, dict, int], dict],
    retries: int,
) -> dict:
    company = compact["company"]
    prompt = (
        "Analyze only the supplied website evidence. Do not use prior knowledge and do not "
        "invent facts. Include only facts directly about the target company, its own employees, "
        "products, actions, locations, or financing. A person mentioned in an interview or an "
        "external organization is not company leadership. A general opinion, event description, "
        "or industry statement is not company news. Every fact must cite exactly one source_url "
        "present in the evidence and include a short verbatim evidence_quote copied from that "
        "source snippet. Write one concise sentence per fact; do not copy an entire specifications "
        "block into the statement. "
        "If evidence is insufficient, return an empty array for that category. Keep the summary "
        f"brief and factual about {company}. Return JSON matching the supplied schema.\n\nEVIDENCE:\n"
        + json.dumps(compact, ensure_ascii=False)
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a careful company research analyst."},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "think": False,
        "format": ANALYSIS_SCHEMA,
        "options": {"temperature": 0, "num_ctx": 4096},
        "keep_alive": "5m",
    }
    response = _transport_with_retries(transport, endpoint, payload, timeout, retries)
    try:
        return json.loads(response["message"]["content"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AnalysisError("model did not return valid JSON") from exc


def _normalize_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _transport_with_retries(
    transport: Callable[[str, dict, int], dict],
    endpoint: str,
    payload: dict,
    timeout: int,
    retries: int,
) -> dict:
    for attempt in range(max(retries, 0) + 1):
        try:
            return transport(endpoint, payload, timeout)
        except AnalysisError:
            if attempt >= max(retries, 0):
                raise
    raise AnalysisError("local Ollama request failed")


def _quote_is_present(
    quote: str, source_url: str, category: str, compact: dict
) -> bool:
    needle = _normalize_text(quote)
    if len(needle) < 12:
        return False
    return any(
        item.get("source_url") == source_url
        and needle in _normalize_text(item.get("snippet", ""))
        for items in compact["evidence"].values()
        for item in items
    )


def _statement_is_supported(statement: str, quote: str) -> bool:
    ignored = {
        "a", "an", "and", "are", "as", "at", "by", "for", "from", "in",
        "is", "it", "of", "on", "or", "the", "their", "this", "to", "was",
        "with", "its",
    }
    statement_words = {
        word for word in re.findall(r"[a-z0-9]+", statement.casefold())
        if len(word) > 2 and word not in ignored
    }
    quote_words = set(re.findall(r"[a-z0-9]+", quote.casefold()))
    if not statement_words:
        return False
    return len(statement_words & quote_words) / len(statement_words) >= 0.55


def _fact_is_sane(category: str, statement: str, quote: str) -> bool:
    combined = f"{statement} {quote}"
    if category == "leadership" and not LEADERSHIP_ROLE_PATTERN.search(statement):
        return False
    if category == "funding" and re.search(r"(?<!\d)0,\d{3}\b", combined):
        return False
    if category == "funding" and not (
        re.search(
            r"\b(?:rais(?:e|ed|ing)|fund(?:ing|ed)?|invest(?:ment|ed|or)?|grant|seed|"
            r"series [a-z]|capital|financ(?:e|ing)|usd|eur|million|billion|thousand)\b|[$€£]",
            statement,
            re.IGNORECASE,
        )
        or re.search(r"\b(?:SBIR|STTR)\b.*\baward", statement, re.IGNORECASE)
    ):
        return False
    if category == "news" and not NEWS_EVENT_PATTERN.search(statement):
        return False
    if category == "technology" and re.search(
        r"\b(?:intellectual integrity|diversity and collaboration|open communication)\b",
        statement,
        re.IGNORECASE,
    ):
        return False
    if re.search(r"\b(?:a|an|the|to|of|for|with)\.$", statement, re.IGNORECASE):
        return False
    if re.search(
        r"\b(?:with|by|from|at)\s+[A-Z][A-Za-z]{0,2}\.\s+"
        r"(?:based|for|to|in|on|and)\b",
        statement,
    ):
        return False
    return True


def _summarize_verified_facts(
    company: str,
    facts: dict[str, list[dict]],
    model: str,
    endpoint: str,
    timeout: int,
    transport: Callable[[str, dict, int], dict],
    retries: int,
) -> str:
    statements = [
        item["statement"]
        for category in CATEGORIES
        for item in facts[category]
    ]
    if not statements:
        return ""
    schema = {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
    }
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": (
                f"Write one concise, non-repetitive factual summary of {company}. "
                "Use only the verified facts below. Do not add information. Return JSON.\n"
                + json.dumps(statements, ensure_ascii=False)
            ),
        }],
        "stream": False,
        "think": False,
        "format": schema,
        "options": {"temperature": 0, "num_ctx": 4096},
        "keep_alive": "5m",
    }
    response = _transport_with_retries(transport, endpoint, payload, timeout, retries)
    try:
        result = json.loads(response["message"]["content"])
        return result.get("summary", "").strip()
    except (KeyError, TypeError, json.JSONDecodeError, AttributeError) as exc:
        raise AnalysisError("model did not return a valid final summary") from exc


def analyze_profile(
    profile: dict,
    model: str = "qwen3:8b",
    endpoint: str = "http://localhost:11434/api/chat",
    timeout: int = 900,
    retries: int = 1,
    transport: Callable[[str, dict, int], dict] = _default_transport,
) -> dict:
    if profile.get("extraction_status") != "evidence_ready":
        return {
            "company": profile.get("company", ""),
            "website": profile.get("website", ""),
            "analysis_status": "no_content_available",
            "model": None,
            "summary": "",
            "facts": {category: [] for category in CATEGORIES},
        }
    merged = {category: [] for category in CATEGORIES}
    globally_seen: set[str] = set()

    def process_group(group: tuple[str, ...], maximum_per_category: int = 5) -> None:
        compact = _compact_profile(profile, group, maximum_per_category)
        if not any(compact["evidence"].values()):
            return
        allowed_urls = {
            item["source_url"]
            for items in compact["evidence"].values()
            for item in items
            if item["source_url"]
        }
        try:
            raw = _model_request(compact, model, endpoint, timeout, transport, retries)
        except AnalysisError as exc:
            if "timeout" not in str(exc).casefold():
                raise
            if len(group) > 1:
                for category in group:
                    process_group((category,), maximum_per_category)
                return
            if maximum_per_category > 2:
                process_group(group, 2)
                return
            raise
        validated = _validate_result(raw, allowed_urls)
        for category in group:
            for item in validated["facts"][category]:
                statement_key = _normalize_text(item["statement"])
                if (
                    _quote_is_present(
                        item["evidence_quote"], item["source_url"], category, compact
                    )
                    and _statement_is_supported(
                        item["statement"], item["evidence_quote"]
                    )
                    and _fact_is_sane(
                        category, item["statement"], item["evidence_quote"]
                    )
                    and statement_key not in globally_seen
                ):
                    merged[category].append(item)
                    globally_seen.add(statement_key)

    for group in EVIDENCE_GROUPS:
        process_group(group)
    summary = _summarize_verified_facts(
        profile.get("company", ""), merged, model, endpoint, timeout, transport, retries
    )
    fact_count = sum(len(items) for items in merged.values())
    return {
        "company": profile.get("company", ""),
        "website": profile.get("website", ""),
        "analysis_status": "analyzed" if fact_count else "no_verified_facts",
        "model": model,
        "summary": summary,
        "facts": merged,
    }


def analyze_evidence_file(
    input_path: str | Path,
    company_name: str,
    model: str = "qwen3:8b",
    **kwargs,
) -> dict:
    source = Path(input_path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    profile = next(
        (item for item in payload.get("profiles", [])
         if item.get("company", "").casefold() == company_name.casefold()),
        None,
    )
    if profile is None:
        raise AnalysisError(f"company not found in evidence: {company_name}")
    return {
        "source_file": str(source),
        "analysis": analyze_profile(profile, model=model, **kwargs),
    }


def export_analysis(payload: dict, export_dir: str | Path) -> Path:
    directory = Path(export_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"company_analysis_{utc_stamp()}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)


def analyze_all_evidence(
    input_path: str | Path,
    checkpoint_path: str | Path,
    model: str = "qwen3:8b",
    progress: Callable[[int, int, str, str], None] | None = None,
    refresh_companies: list[str] | None = None,
    **kwargs,
) -> dict:
    source = Path(input_path)
    evidence = json.loads(source.read_text(encoding="utf-8"))
    profiles = evidence.get("profiles", [])
    checkpoint = Path(checkpoint_path)
    state = None
    if checkpoint.exists():
        try:
            candidate = json.loads(checkpoint.read_text(encoding="utf-8"))
            if candidate.get("source_file") == str(source) and candidate.get("model") == model:
                state = candidate
        except json.JSONDecodeError:
            state = None
    if state is None:
        state = {
            "source_file": str(source),
            "model": model,
            "results": {},
            "errors": {},
        }

    requested_refresh = {name.casefold() for name in (refresh_companies or [])}
    known_companies = {
        profile.get("company", "").casefold() for profile in profiles
    }
    unknown_refresh = sorted(requested_refresh - known_companies)
    if unknown_refresh:
        raise AnalysisError(
            "company not found in evidence: " + ", ".join(unknown_refresh)
        )
    for company in list(state["results"]):
        if company.casefold() in requested_refresh:
            state["results"].pop(company, None)
            state["errors"].pop(company, None)

    total = len(profiles)
    for index, profile in enumerate(profiles, start=1):
        company = profile.get("company", "")
        if company in state["results"]:
            if progress:
                progress(index, total, company, "already_completed")
            continue
        try:
            result = analyze_profile(profile, model=model, **kwargs)
        except AnalysisError as exc:
            state["errors"][company] = str(exc)
            status = "failed"
        else:
            state["results"][company] = result
            state["errors"].pop(company, None)
            status = result["analysis_status"]
        _write_json_atomic(checkpoint, state)
        if progress:
            progress(index, total, company, status)

    ordered_results = [
        state["results"][profile.get("company", "")]
        for profile in profiles
        if profile.get("company", "") in state["results"]
    ]
    return {
        "source_file": str(source),
        "model": model,
        "company_count": total,
        "completed": len(ordered_results),
        "analyzed": sum(
            item["analysis_status"] == "analyzed" for item in ordered_results
        ),
        "no_content_available": sum(
            item["analysis_status"] == "no_content_available" for item in ordered_results
        ),
        "no_verified_facts": sum(
            item["analysis_status"] == "no_verified_facts" for item in ordered_results
        ),
        "failed": len(state["errors"]),
        "errors": state["errors"],
        "analyses": ordered_results,
        "checkpoint": str(checkpoint),
    }


def export_analysis_batch(payload: dict, export_dir: str | Path) -> Path:
    directory = Path(export_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"company_analysis_all_{utc_stamp()}.json"
    _write_json_atomic(path, payload)
    return path
