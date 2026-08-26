from __future__ import annotations

import csv
import html
import json
from datetime import datetime, timezone
from pathlib import Path


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def latest_analysis(export_dir: str | Path) -> Path:
    candidates = sorted(Path(export_dir).glob("company_analysis_all_*.json"))
    if not candidates:
        raise FileNotFoundError(
            "no company_analysis_all_*.json export found; run analyze --all first"
        )
    return candidates[-1]


def _all_facts(analysis: dict) -> list[tuple[str, dict]]:
    return [
        (category, fact)
        for category, facts in analysis.get("facts", {}).items()
        for fact in facts
    ]


def score_company(analysis: dict) -> dict:
    status = analysis.get("analysis_status", "unknown")
    if status != "analyzed":
        return {"score": 0, "fit": "not_scored", "reasons": [], "modifier": 0}
    assessment = analysis.get("commercial_assessment", {})
    if assessment.get("assessment_status") != "assessed":
        return {"score": 0, "fit": "not_scored", "reasons": [], "modifier": 0}
    scoring = assessment.get("customer_partner_scoring", {})
    score = scoring.get("base_score", 0)
    if not isinstance(score, int):
        score = 0
    geography = assessment.get("geography", {})
    modifier = geography.get("affinity_modifier", 0)
    if not isinstance(modifier, int):
        modifier = 0
    if geography.get("eligibility") == "excluded_geography":
        return {
            "score": 0, "fit": "excluded", "modifier": 0,
            "reasons": [{"signal": "geography", "points": 0, "label": "Excluded by verified geography policy"}],
        }
    reasons = [{
        "signal": key,
        "points": scoring.get(key, 0),
        "label": label,
    } for key, label in (
        ("human_perception_need", "Human-perception need"),
        ("integration_fit", "Integration fit"),
        ("commercial_capacity", "Commercial capacity"),
        ("timing_and_access", "Timing and access"),
    )]
    fit = "high" if score >= 7 else "medium" if score >= 4 else "low"
    potential = assessment.get("potential_score", score)
    if not isinstance(potential, int):
        potential = score
    potential = max(score, min(10, potential))
    return {
        "score": score, "potential_score": potential, "fit": fit,
        "reasons": reasons, "modifier": modifier,
    }


def build_report(analysis_payload: dict) -> dict:
    companies = []
    for analysis in analysis_payload.get("analyses", []):
        scored = score_company(analysis)
        facts = _all_facts(analysis)
        sources = list(dict.fromkeys(
            fact.get("source_url", "") for _, fact in facts if fact.get("source_url")
        ))
        companies.append({
            "company": analysis.get("company", ""),
            "website": analysis.get("website", ""),
            "status": analysis.get("analysis_status", "unknown"),
            "summary": analysis.get("summary", ""),
            "score": scored["score"],
            "potential_score": scored.get("potential_score", scored["score"]),
            "fit": scored["fit"],
            "reasons": scored["reasons"],
            "affinity_modifier": scored["modifier"],
            "commercial_assessment": analysis.get("commercial_assessment", {}),
            "facts": analysis.get("facts", {}),
            "fact_count": len(facts),
            "sources": sources,
        })
    confidence_order = {"high": 0, "medium": 1, "low": 2, "insufficient_evidence": 3}
    companies.sort(key=lambda item: (
        item["status"] != "analyzed",
        -item["score"],
        -item["potential_score"],
        confidence_order.get(
            item.get("commercial_assessment", {}).get(
                "confidence", "insufficient_evidence"
            ),
            4,
        ),
        item["company"].casefold(),
    ))
    return {
        "source_file": analysis_payload.get("source_file", ""),
        "company_count": len(companies),
        "companies": companies,
    }


def _write_csv(report: dict, path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=(
            "rank", "company", "relationship_types", "confirmed_score", "potential_score",
            "affinity_modifier", "fit", "eligibility", "confidence", "status",
            "verification_status", "verification_questions",
            "summary", "relationship_hypothesis", "hypervision_relevance",
            "first_engagement", "scoring_reasons", "fact_count", "website", "sources",
        ))
        writer.writeheader()
        rank = 0
        for company in report["companies"]:
            if company["status"] == "analyzed" and company["score"] > 0:
                rank += 1
                displayed_rank = rank
            else:
                displayed_rank = ""
            writer.writerow({
                "rank": displayed_rank,
                "company": company["company"],
                "relationship_types": "; ".join(
                    company["commercial_assessment"].get("relationship_types", [])
                ),
                "confirmed_score": company["score"],
                "potential_score": company["potential_score"],
                "affinity_modifier": company["affinity_modifier"],
                "fit": company["fit"],
                "eligibility": company["commercial_assessment"].get(
                    "geography", {}
                ).get("eligibility", "unverified"),
                "confidence": company["commercial_assessment"].get(
                    "confidence", "insufficient_evidence"
                ),
                "status": company["status"],
                "verification_status": company["commercial_assessment"].get(
                    "verification_status", "research_needed"
                ),
                "verification_questions": "; ".join(
                    company["commercial_assessment"].get("verification_questions", [])
                ),
                "summary": company["summary"],
                "relationship_hypothesis": company["commercial_assessment"].get(
                    "relationship_hypothesis", ""
                ),
                "hypervision_relevance": company["commercial_assessment"].get(
                    "hypervision_relevance", ""
                ),
                "first_engagement": company["commercial_assessment"].get(
                    "first_engagement", ""
                ),
                "scoring_reasons": "; ".join(
                    f"+{reason['points']} {reason['label']}"
                    for reason in company["reasons"]
                ),
                "fact_count": company["fact_count"],
                "website": company["website"],
                "sources": " | ".join(company["sources"]),
            })


def _company_card(company: dict, rank: int | None) -> str:
    name = html.escape(company["company"])
    website = html.escape(company["website"], quote=True)
    title = f'<a href="{website}" target="_blank">{name}</a>' if website else name
    assessment = company.get("commercial_assessment", {})
    reasons = "".join(
        f"<li><strong>+{reason['points']}</strong> {html.escape(reason['label'])}</li>"
        for reason in company["reasons"]
    ) or "<li>No relevance signals scored</li>"
    fact_sections = []
    for category, facts in company["facts"].items():
        if not facts:
            continue
        items = "".join(
            f'<li>{html.escape(fact.get("statement", ""))} '
            f'<a href="{html.escape(fact.get("source_url", ""), quote=True)}" '
            f'target="_blank">source</a></li>'
            for fact in facts
        )
        fact_sections.append(
            f"<details><summary>{html.escape(category.title())} ({len(facts)})</summary>"
            f"<ul>{items}</ul></details>"
        )
    rank_text = f"#{rank}" if rank is not None else "—"
    relationships = ", ".join(assessment.get("relationship_types", [])) or "not assessed"
    hypothesis = html.escape(assessment.get("relationship_hypothesis", ""))
    relevance = html.escape(assessment.get("hypervision_relevance", ""))
    first_engagement = html.escape(assessment.get("first_engagement", ""))
    confidence = html.escape(assessment.get("confidence", "insufficient_evidence"))
    eligibility = html.escape(
        assessment.get("geography", {}).get("eligibility", "unverified")
    )
    verification_status = html.escape(
        assessment.get("verification_status", "research_needed")
    )
    questions = "".join(
        f"<li>{html.escape(question)}</li>"
        for question in assessment.get("verification_questions", [])
    ) or "<li>No verification plan available.</li>"
    return f"""
    <article class="card {html.escape(company['fit'])}">
      <div class="card-head"><span class="rank">{rank_text}</span><h2>{title}</h2>
      <span class="score">{company['score']}/10 confirmed · up to {company['potential_score']}/10</span></div>
      <div class="meta"><span>{html.escape(company['fit'])} fit</span>
      <span>{html.escape(company['status'])}</span><span>{eligibility}</span>
      <span>{confidence} confidence</span><span>{verification_status}</span>
      <span>{company['fact_count']} facts</span></div>
      <p>{html.escape(company['summary'] or 'No verified summary available.')}</p>
      <h3>Relationship type</h3><p>{html.escape(relationships)}</p>
      <h3>Commercial hypothesis</h3><p>{hypothesis or 'Insufficient evidence.'}</p>
      <h3>HyperVision relevance</h3><p>{relevance or 'Insufficient evidence.'}</p>
      <h3>Scoring</h3><ul>{reasons}</ul>
      <h3>First engagement</h3><p>{first_engagement or 'Further research required.'}</p>
      <h3>What to verify</h3><ul>{questions}</ul>
      {''.join(fact_sections)}
    </article>"""


def _write_html(report: dict, path: Path) -> None:
    analyzed_rank = 0
    cards = []
    for company in report["companies"]:
        rank = None
        if company["status"] == "analyzed" and company["score"] > 0:
            analyzed_rank += 1
            rank = analyzed_rank
        cards.append(_company_card(company, rank))
    high = sum(item["fit"] == "high" for item in report["companies"])
    medium = sum(item["fit"] == "medium" for item in report["companies"])
    unavailable = sum(item["status"] != "analyzed" for item in report["companies"])
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>HyperVision Company Intelligence Report</title>
<style>
body{{font-family:Arial,sans-serif;background:#f4f6f8;color:#17202a;margin:0}}
main{{max-width:1100px;margin:auto;padding:28px 18px}}h1{{margin-bottom:6px}}
.stats{{display:flex;gap:12px;flex-wrap:wrap;margin:20px 0}}.stat{{background:white;padding:14px 18px;border-radius:10px}}
.card{{background:white;border-left:6px solid #aab2bd;border-radius:12px;padding:20px;margin:16px 0;box-shadow:0 2px 8px #0001}}
.card.high{{border-color:#168f5b}}.card.medium{{border-color:#d49b13}}.card.low{{border-color:#74828f}}
.card-head{{display:flex;align-items:center;gap:12px}}.card-head h2{{flex:1;margin:0}}a{{color:#1261a0}}
.rank{{font-size:18px;color:#65727e}}.score{{font-size:22px;font-weight:bold}}.meta{{display:flex;gap:8px;margin:10px 0}}
.meta span{{background:#edf1f4;border-radius:20px;padding:5px 10px;font-size:13px}}details{{margin:8px 0}}li{{margin:6px 0}}
</style></head><body><main><h1>HyperVision Company Intelligence</h1>
<p>Companies assessed against the HyperVision decision profile using verified, source-linked facts.</p>
<section class="stats"><div class="stat"><strong>{report['company_count']}</strong><br>companies</div>
<div class="stat"><strong>{high}</strong><br>high fit</div><div class="stat"><strong>{medium}</strong><br>medium fit</div>
<div class="stat"><strong>{unavailable}</strong><br>not scored</div></section>
{''.join(cards)}</main></body></html>"""
    path.write_text(document, encoding="utf-8")


def export_report(analysis_path: str | Path, export_dir: str | Path) -> tuple[Path, Path, dict]:
    source = Path(analysis_path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    report = build_report(payload)
    directory = Path(export_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = utc_stamp()
    html_path = directory / f"company_report_{stamp}.html"
    csv_path = directory / f"company_report_{stamp}.csv"
    _write_html(report, html_path)
    _write_csv(report, csv_path)
    return html_path, csv_path, report
