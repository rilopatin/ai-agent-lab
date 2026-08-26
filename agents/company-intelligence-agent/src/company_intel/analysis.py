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
ANALYSIS_VERSION = "2.4.2-verification-aware-scoring"
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

RELATIONSHIP_TYPES = (
    "customer", "technology_partner", "integrator_or_channel", "investor",
    "supplier", "grant_partner", "competitor_or_benchmark", "no_supported_fit",
)

COMMERCIAL_SCHEMA = {
    "type": "object",
    "properties": {
        "relationship_types": {
            "type": "array",
            "items": {"type": "string", "enum": list(RELATIONSHIP_TYPES)},
        },
        "relationship_hypothesis": {"type": "string"},
        "need_evidence_refs": {"type": "array", "items": {"type": "string"}},
        "hypervision_relevance": {"type": "string"},
        "integration_dependencies": {"type": "array", "items": {"type": "string"}},
        "first_engagement": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "string"}},
        "potential_score": {"type": "integer", "minimum": 0, "maximum": 10},
        "potential_score_rationale": {"type": "string"},
        "verification_questions": {"type": "array", "items": {"type": "string"}},
        "customer_partner_scoring": {
            "type": "object",
            "properties": {
                "human_perception_need": {"type": "integer", "minimum": 0, "maximum": 4},
                "integration_fit": {"type": "integer", "minimum": 0, "maximum": 3},
                "commercial_capacity": {"type": "integer", "minimum": 0, "maximum": 2},
                "timing_and_access": {"type": "integer", "minimum": 0, "maximum": 1},
                "rationale": {"type": "string"},
            },
            "required": [
                "human_perception_need", "integration_fit", "commercial_capacity",
                "timing_and_access", "rationale",
            ],
        },
        "investor_scoring": {
            "type": "object",
            "properties": {
                "applicable": {"type": "boolean"},
                "thesis_fit": {"type": "integer", "minimum": 0, "maximum": 3},
                "stage_and_check_fit": {"type": "integer", "minimum": 0, "maximum": 2},
                "strategic_leverage": {"type": "integer", "minimum": 0, "maximum": 2},
                "track_record_and_capacity": {"type": "integer", "minimum": 0, "maximum": 2},
                "timing_and_geography": {"type": "integer", "minimum": 0, "maximum": 1},
                "rationale": {"type": "string"},
            },
            "required": [
                "applicable", "thesis_fit", "stage_and_check_fit", "strategic_leverage",
                "track_record_and_capacity", "timing_and_geography", "rationale",
            ],
        },
        "geography": {
            "type": "object",
            "properties": {
                "incorporation_country": {"type": "string"},
                "headquarters_country": {"type": "string"},
                "russian_company": {"type": "string", "enum": ["yes", "no", "unknown"]},
                "eligibility": {"type": "string", "enum": ["eligible", "excluded_geography", "unverified"]},
                "israel_connection": {"type": "string", "enum": ["verified", "not_verified"]},
                "ukraine_connection": {"type": "string", "enum": ["verified", "not_verified"]},
                "affinity_modifier": {"type": "integer", "minimum": 0, "maximum": 2},
                "evidence_refs": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "incorporation_country", "headquarters_country", "russian_company",
                "eligibility", "israel_connection", "ukraine_connection",
                "affinity_modifier", "evidence_refs",
            ],
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low", "insufficient_evidence"]},
        "missing_evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "relationship_types", "relationship_hypothesis", "need_evidence_refs",
        "hypervision_relevance", "integration_dependencies", "first_engagement",
        "risks", "customer_partner_scoring", "investor_scoring", "geography",
        "confidence", "missing_evidence", "potential_score",
        "potential_score_rationale", "verification_questions",
    ],
}

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


def _empty_assessment(status: str = "not_assessed") -> dict:
    return {
        "assessment_status": status,
        "relationship_types": [],
        "relationship_hypothesis": "",
        "need_evidence_refs": [],
        "hypervision_relevance": "",
        "integration_dependencies": [],
        "first_engagement": "",
        "risks": [],
        "confirmed_score": 0,
        "potential_score": 0,
        "potential_score_rationale": "",
        "verification_status": "research_needed",
        "verification_questions": [],
        "customer_partner_scoring": {
            "human_perception_need": 0, "integration_fit": 0,
            "commercial_capacity": 0, "timing_and_access": 0,
            "base_score": 0, "rationale": "",
        },
        "investor_scoring": {
            "applicable": False, "thesis_fit": 0, "stage_and_check_fit": 0,
            "strategic_leverage": 0, "track_record_and_capacity": 0,
            "timing_and_geography": 0, "base_score": 0, "rationale": "",
        },
        "geography": {
            "incorporation_country": "", "headquarters_country": "",
            "russian_company": "unknown", "eligibility": "unverified",
            "israel_connection": "not_verified", "ukraine_connection": "not_verified",
            "affinity_modifier": 0, "evidence_refs": [],
        },
        "confidence": "insufficient_evidence",
        "missing_evidence": [],
    }


def _load_decision_profile(path: str | Path = "config/hypervision_decision_profile.json") -> dict:
    profile_path = Path(path)
    if not profile_path.exists():
        raise AnalysisError(f"HyperVision decision profile not found: {profile_path}")
    try:
        return json.loads(profile_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AnalysisError("HyperVision decision profile is invalid JSON") from exc


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


def _commercial_assessment(
    company: str,
    facts: dict[str, list[dict]],
    model: str,
    endpoint: str,
    timeout: int,
    transport: Callable[[str, dict, int], dict],
    retries: int,
    decision_profile_path: str | Path,
) -> dict:
    catalog = []
    allowed_refs: set[str] = set()
    for category in CATEGORIES:
        for index, fact in enumerate(facts[category], start=1):
            fact_id = f"{category}.{index}"
            allowed_refs.add(fact_id)
            catalog.append({
                "fact_id": fact_id,
                "category": category,
                "statement": fact["statement"],
                "source_url": fact["source_url"],
                "evidence_quote": fact["evidence_quote"],
                "confidence": fact["confidence"],
            })
    if not catalog:
        return _empty_assessment("insufficient_evidence")
    profile = _load_decision_profile(decision_profile_path)
    prompt = (
        "Apply the supplied HyperVision decision profile to the verified facts. "
        "Do not reward keywords, sector similarity, defense activity, drones, cameras, "
        "sensors, autonomy or funding by themselves. A CUSTOMER hypothesis requires a concrete "
        "human-operator visual-perception need and a plausible HyperVision integration path. "
        "A TECHNOLOGY-PARTNER hypothesis does not require the company itself to buy an HMD: it "
        "may qualify when verified facts show that it supplies a complementary component needed "
        "for a supported joint architecture, such as panoramic/EO-IR imaging, low-latency video, "
        "embedded electronics, HMD productization, simulation integration, manufacturing, "
        "communications or a remote platform. In that case, cite the component and supported "
        "downstream application, describe the division of responsibilities, and score only what "
        "the evidence supports. Do not classify the target as a CUSTOMER merely because its own "
        "product has downstream operators: customer requires evidence that the target itself could "
        "buy, integrate or operate HyperVision's visual interface. Do not classify a company as an "
        "INVESTOR merely because it raised money: investor requires evidence that it deploys capital "
        "into other companies. Use the score anchors exactly. Award timing_and_access=1 only for an "
        "explicit current relationship, warm route, active joint work, procurement or named opportunity. "
        "Always provide a concrete first engagement, integration dependencies, at least one risk, and "
        "missing evidence. Also estimate potential_score: the maximum defensible customer/partner score "
        "if specific currently-unknown facts are positively verified. It must never be lower than the "
        "evidence-backed score, must not assume unknown facts are true, and its rationale must name the "
        "exact assumptions. Provide concise verification questions that would confirm or reject those "
        "assumptions. The relationship_hypothesis must be an explanatory sentence, not a label. "
        "Keep customer, partner and investor hypotheses separate. Use only "
        "supplied fact IDs in evidence_refs. "
        "Do not infer incorporation, founder origin, nationality, religion, ownership or Israel/"
        "Ukraine affinity from a name. If geography is not explicit, mark it unverified. "
        "A founder's Russian birthplace or past alone is not a company exclusion. Exclude only "
        "when verified facts show that the current company itself meets the Russian-company rule. "
        "Scores must reflect evidence, and missing evidence must score zero rather than be guessed. "
        "Return concise JSON matching the schema.\n\n"
        f"TARGET COMPANY: {company}\n"
        "HYPERVISION DECISION PROFILE:\n"
        + json.dumps(profile, ensure_ascii=False)
        + "\n\nVERIFIED FACT CATALOG:\n"
        + json.dumps(catalog, ensure_ascii=False)
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are an evidence-grounded B2B business analyst."},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "think": False,
        "format": COMMERCIAL_SCHEMA,
        "options": {"temperature": 0, "num_ctx": 4096},
        "keep_alive": "5m",
    }
    response = _transport_with_retries(transport, endpoint, payload, timeout, retries)
    try:
        raw = json.loads(response["message"]["content"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AnalysisError("model did not return a valid commercial assessment") from exc
    if not isinstance(raw, dict):
        raise AnalysisError("model returned an invalid commercial assessment")

    result = _empty_assessment("assessed")
    result["relationship_types"] = [
        value for value in raw.get("relationship_types", [])
        if value in RELATIONSHIP_TYPES
    ]
    for key in (
        "relationship_hypothesis", "hypervision_relevance", "first_engagement",
    ):
        result[key] = str(raw.get(key, "")).strip()
    for key in ("integration_dependencies", "risks", "missing_evidence", "verification_questions"):
        values = raw.get(key, [])
        result[key] = [str(value).strip() for value in values if str(value).strip()][:8]
    result["need_evidence_refs"] = [
        ref for ref in raw.get("need_evidence_refs", [])
        if ref in allowed_refs and ref.split(".", 1)[0] in {
            "products", "technology", "applications", "locations",
        }
    ]

    customer = raw.get("customer_partner_scoring", {})
    limits = {
        "human_perception_need": 4, "integration_fit": 3,
        "commercial_capacity": 2, "timing_and_access": 1,
    }
    for key, maximum in limits.items():
        value = customer.get(key, 0)
        result["customer_partner_scoring"][key] = (
            max(0, min(maximum, value)) if isinstance(value, int) else 0
        )
    has_commercial_evidence = bool(facts.get("funding")) or any(
        re.search(
            r"\b(?:revenue|sales|paid contract|customer contract|procurement|"
            r"commercialization|series [a-z]|grant|award)\b",
            f"{fact.get('statement', '')} {fact.get('evidence_quote', '')}",
            re.IGNORECASE,
        )
        for category in ("products", "applications", "news")
        for fact in facts.get(category, [])
    )
    if not has_commercial_evidence:
        result["customer_partner_scoring"]["commercial_capacity"] = 0
    result["customer_partner_scoring"]["rationale"] = str(
        customer.get("rationale", "")
    ).strip()
    result["customer_partner_scoring"]["base_score"] = sum(
        result["customer_partner_scoring"][key] for key in limits
    )
    result["confirmed_score"] = result["customer_partner_scoring"]["base_score"]
    result["potential_score"] = result["confirmed_score"]
    result["potential_score_rationale"] = str(
        raw.get("potential_score_rationale", "")
    ).strip()

    investor = raw.get("investor_scoring", {})
    result["investor_scoring"]["applicable"] = investor.get("applicable") is True
    investor_limits = {
        "thesis_fit": 3, "stage_and_check_fit": 2, "strategic_leverage": 2,
        "track_record_and_capacity": 2, "timing_and_geography": 1,
    }
    for key, maximum in investor_limits.items():
        value = investor.get(key, 0)
        result["investor_scoring"][key] = (
            max(0, min(maximum, value))
            if isinstance(value, int) and result["investor_scoring"]["applicable"] else 0
        )
    result["investor_scoring"]["rationale"] = str(
        investor.get("rationale", "")
    ).strip()
    result["investor_scoring"]["base_score"] = sum(
        result["investor_scoring"][key] for key in investor_limits
    )
    if not result["investor_scoring"]["applicable"]:
        result["relationship_types"] = [
            value for value in result["relationship_types"] if value != "investor"
        ]

    customer_denial_text = " ".join(
        [result["customer_partner_scoring"]["rationale"]]
        + result["risks"] + result["missing_evidence"]
    ).casefold()
    if (
        "customer" in result["relationship_types"]
        and re.search(
            r"(?:\b(?:no|without|lack(?:s|ing)?)\b.{0,60}\bevidence\b.{0,140}"
            r"(?:\bcustomer\b|\bbuy\w*\b|\bpurchas\w*\b|\bintegrat\w*\b|\boperat\w*\b))|"
            r"(?:\bno evidence shows\b.{0,140}\bcustomer\b)",
            customer_denial_text,
        )
    ):
        result["relationship_types"] = [
            value for value in result["relationship_types"] if value != "customer"
        ]

    geography = raw.get("geography", {})
    geography_refs = [
        ref for ref in geography.get("evidence_refs", [])
        if ref in allowed_refs and ref.startswith("locations.")
    ]
    for key in ("incorporation_country", "headquarters_country"):
        result["geography"][key] = str(geography.get(key, "")).strip()
    for key, allowed in {
        "russian_company": {"yes", "no", "unknown"},
        "eligibility": {"eligible", "excluded_geography", "unverified"},
        "israel_connection": {"verified", "not_verified"},
        "ukraine_connection": {"verified", "not_verified"},
    }.items():
        value = geography.get(key)
        if value in allowed:
            result["geography"][key] = value
    modifier = geography.get("affinity_modifier", 0)
    result["geography"]["affinity_modifier"] = (
        max(0, min(2, modifier)) if isinstance(modifier, int) else 0
    )
    result["geography"]["evidence_refs"] = geography_refs
    if not geography_refs:
        result["geography"].update({
            "incorporation_country": "", "headquarters_country": "",
            "russian_company": "unknown", "eligibility": "unverified",
            "israel_connection": "not_verified", "ukraine_connection": "not_verified",
            "affinity_modifier": 0,
        })
    elif result["geography"]["russian_company"] == "yes":
        result["geography"]["eligibility"] = "excluded_geography"
        result["geography"]["affinity_modifier"] = 0
        result["customer_partner_scoring"]["base_score"] = 0
        result["confirmed_score"] = 0
        result["potential_score"] = 0
        result["investor_scoring"]["base_score"] = 0

    confidence = raw.get("confidence")
    if confidence in {"high", "medium", "low", "insufficient_evidence"}:
        result["confidence"] = confidence
    if not result["integration_dependencies"]:
        result["integration_dependencies"] = [
            "Technical interfaces and the division of responsibilities are not yet verified."
        ]
    if not re.search(
        r"\b(?:propose|schedule|conduct|validate|arrange|request|offer|demonstrate|"
        r"introduce|discuss|workshop|meeting|pilot|feasibility)\b",
        result["first_engagement"], re.IGNORECASE,
    ):
        result["first_engagement"] = (
            "Propose a technical workshop to validate the joint architecture, interfaces, "
            "responsibility split, and a paid pilot path."
        )
    if not result["risks"]:
        result["risks"] = [
            "Commercial interest and integration requirements are not yet verified."
        ]
    if result["geography"]["eligibility"] == "unverified":
        geography_gap = "Company incorporation, headquarters and ownership require verification."
        if geography_gap not in result["missing_evidence"]:
            result["missing_evidence"].append(geography_gap)
    if result["missing_evidence"] and result["confidence"] == "high":
        result["confidence"] = "medium"
    if (
        result["confidence"] == "insufficient_evidence"
        and "technology_partner" in result["relationship_types"]
        and result["customer_partner_scoring"]["base_score"] >= 4
        and result["need_evidence_refs"]
    ):
        result["confidence"] = "medium"
    if not result["verification_questions"]:
        result["verification_questions"] = [
            f"Can the company confirm: {gap.rstrip('.')}?"
            for gap in result["missing_evidence"][:5]
        ]
    verification_text = " ".join(
        result["missing_evidence"] + result["verification_questions"]
    ).casefold()
    lift_assumptions = []
    derived_potential = result["confirmed_score"]
    scoring = result["customer_partner_scoring"]
    if scoring["timing_and_access"] == 0 and re.search(
        r"\b(?:current relationship|warm access|active joint work|procurement|"
        r"named opportunity|named buyer|commercial opportunity)\b", verification_text,
    ):
        derived_potential += 1
        lift_assumptions.append("a current route, active opportunity, or procurement path is verified")
    if scoring["commercial_capacity"] < 2 and re.search(
        r"\b(?:budget|commercial capacity|purchasing capacity|procurement budget|"
        r"funded pilot|customer contract)\b", verification_text,
    ):
        derived_potential += 2 - scoring["commercial_capacity"]
        lift_assumptions.append("budget or procurement capacity for a paid pilot is verified")
    if scoring["integration_fit"] < 3 and re.search(
        r"\b(?:integrat|interface|compatib|architecture|responsibilit)\w*\b",
        verification_text,
    ):
        derived_potential += 3 - scoring["integration_fit"]
        lift_assumptions.append("technical interfaces and architecture compatibility are verified")
    if scoring["human_perception_need"] < 4 and re.search(
        r"\b(?:operator pain|operator workflow|field.of.view pain|quantified need|"
        r"mission.critical perception)\b", verification_text,
    ):
        derived_potential += 4 - scoring["human_perception_need"]
        lift_assumptions.append("the target's mission-critical operator perception need is verified")
    derived_potential = min(10, derived_potential)
    if derived_potential > result["potential_score"]:
        result["potential_score"] = derived_potential
        result["potential_score_rationale"] = (
            "Conditional upper score if " + "; and if ".join(lift_assumptions) + "."
        )
    if result["geography"]["eligibility"] == "excluded_geography":
        result["verification_status"] = "excluded"
    elif result["confirmed_score"] >= 7 and result["confidence"] in {"high", "medium"}:
        result["verification_status"] = "qualified"
    elif result["potential_score"] >= 7 or result["confirmed_score"] >= 4:
        result["verification_status"] = "promising_needs_verification"
    elif result["potential_score"] <= 3 and result["confidence"] in {"high", "medium"}:
        result["verification_status"] = "low_fit"
    else:
        result["verification_status"] = "research_needed"
    return result


def analyze_profile(
    profile: dict,
    model: str = "qwen3:8b",
    endpoint: str = "http://localhost:11434/api/chat",
    timeout: int = 900,
    retries: int = 1,
    decision_profile_path: str | Path = "config/hypervision_decision_profile.json",
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
            "commercial_assessment": _empty_assessment("insufficient_evidence"),
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
    commercial_assessment = _empty_assessment("insufficient_evidence")
    if fact_count:
        try:
            commercial_assessment = _commercial_assessment(
                profile.get("company", ""), merged, model, endpoint, timeout,
                transport, retries, decision_profile_path,
            )
        except AnalysisError as exc:
            commercial_assessment = _empty_assessment("assessment_failed")
            commercial_assessment["missing_evidence"] = [str(exc)]
    return {
        "company": profile.get("company", ""),
        "website": profile.get("website", ""),
        "analysis_status": "analyzed" if fact_count else "no_verified_facts",
        "model": model,
        "summary": summary,
        "facts": merged,
        "commercial_assessment": commercial_assessment,
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
            if (
                candidate.get("source_file") == str(source)
                and candidate.get("model") == model
                and candidate.get("analysis_version") == ANALYSIS_VERSION
            ):
                state = candidate
        except json.JSONDecodeError:
            state = None
    if state is None:
        state = {
            "source_file": str(source),
            "model": model,
            "analysis_version": ANALYSIS_VERSION,
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
        "analysis_version": ANALYSIS_VERSION,
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
        "commercially_assessed": sum(
            item.get("commercial_assessment", {}).get("assessment_status") == "assessed"
            for item in ordered_results
        ),
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
