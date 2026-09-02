from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from .exporting import export_scan
from .fetch import FetchError, PageFetcher
from .crawling import CompanySiteCrawler, export_access_audit, export_crawls
from .extraction import export_extraction, extract_crawl_file, latest_crawl
from .monitor import PortfolioMonitor
from .robots_audit import RobotsInspector, export_robots_audit
from .storage import SQLiteStore
from .analysis import (
    AnalysisError, analyze_all_evidence, analyze_evidence_file,
    export_analysis, export_analysis_batch, latest_evidence,
)
from .reporting import export_report, latest_analysis
from .publishing import PublishError, latest_report_pair, publish_report_pair
from .weekly import WeeklyRunError, install_windows_weekly_task, run_weekly_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HyperVision company intelligence agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan = subparsers.add_parser("scan", help="scan and compare a portfolio source")
    scan.add_argument("--source-name", default="GENIUS NY")
    scan.add_argument("--source-url", default="https://geniusny.com/portfolio/")
    scan.add_argument("--database", default="data/company_intelligence.db")
    scan.add_argument("--export-dir", default="data/exports")
    crawl = subparsers.add_parser("crawl", help="crawl company websites from the latest scan")
    crawl.add_argument("--database", default="data/company_intelligence.db")
    crawl.add_argument("--export-dir", default="data/exports")
    crawl.add_argument("--source-url", default="https://geniusny.com/portfolio/")
    crawl.add_argument(
        "--limit", type=int, default=0,
        help="maximum companies to crawl; 0 means the entire latest portfolio",
    )
    crawl.add_argument("--max-pages", type=int, default=15)
    crawl.add_argument("--max-depth", type=int, default=2)
    crawl.add_argument("--request-delay", type=float, default=1.0)
    crawl.add_argument("--rate-limit-retries", type=int, default=2)
    crawl.add_argument("--rate-limit-backoff", type=float, default=15.0)
    crawl.add_argument("--url-overrides", default="config/company_url_overrides.json")
    crawl.add_argument(
        "--company", action="append", default=[],
        help="crawl a named company; may be supplied more than once",
    )
    audit = subparsers.add_parser("audit-access", help="diagnose access to every company site")
    audit.add_argument("--database", default="data/company_intelligence.db")
    audit.add_argument("--export-dir", default="data/exports")
    audit.add_argument("--source-url", default="https://geniusny.com/portfolio/")
    audit.add_argument("--limit", type=int, default=0, help="0 means all companies")
    audit.add_argument("--max-pages", type=int, default=3)
    audit.add_argument("--max-depth", type=int, default=1)
    audit.add_argument("--request-delay", type=float, default=2.0)
    audit.add_argument("--rate-limit-retries", type=int, default=0)
    audit.add_argument("--rate-limit-backoff", type=float, default=15.0)
    audit.add_argument("--url-overrides", default="config/company_url_overrides.json")
    robots = subparsers.add_parser("audit-robots", help="inspect robots.txt rules")
    robots.add_argument("--database", default="data/company_intelligence.db")
    robots.add_argument("--export-dir", default="data/exports")
    robots.add_argument("--source-url", default="https://geniusny.com/portfolio/")
    robots.add_argument("--url-overrides", default="config/company_url_overrides.json")
    robots.add_argument(
        "--company", action="append", default=[],
        help="inspect a named company; may be supplied more than once",
    )
    extract = subparsers.add_parser(
        "extract", help="prepare structured evidence from a company crawl"
    )
    extract.add_argument("--input", help="company_sites JSON; defaults to latest export")
    extract.add_argument("--export-dir", default="data/exports")
    analyze = subparsers.add_parser(
        "analyze", help="analyze company evidence with a local Ollama model"
    )
    target = analyze.add_mutually_exclusive_group(required=True)
    target.add_argument("--company", help="exact company name")
    target.add_argument("--all", action="store_true", help="analyze the entire evidence file")
    analyze.add_argument("--input", help="company_evidence JSON; defaults to latest export")
    analyze.add_argument("--export-dir", default="data/exports")
    analyze.add_argument("--model", default="qwen3:8b")
    analyze.add_argument("--ollama-url", default="http://localhost:11434/api/chat")
    analyze.add_argument("--timeout", type=int, default=900)
    analyze.add_argument("--request-retries", type=int, default=1)
    analyze.add_argument(
        "--checkpoint", default="data/analysis/company_analysis_checkpoint.json"
    )
    analyze.add_argument(
        "--refresh-company", action="append", default=[],
        help="with --all, force a named company to be analyzed again",
    )
    report = subparsers.add_parser(
        "report", help="create a human-readable HyperVision relevance report"
    )
    report.add_argument("--input", help="analysis JSON; defaults to latest full analysis")
    report.add_argument("--export-dir", default="data/exports")
    publish = subparsers.add_parser(
        "publish", help="publish the latest report pair to a Dropbox-synced folder"
    )
    publish.add_argument("--dropbox-dir", required=True)
    publish.add_argument("--export-dir", default="data/exports")
    weekly = subparsers.add_parser(
        "run-weekly", help="run the complete company intelligence workflow and publish it"
    )
    weekly.add_argument("--dropbox-dir", required=True)
    weekly.add_argument("--export-dir", default="data/exports")
    weekly.add_argument("--database", default="data/company_intelligence.db")
    weekly.add_argument(
        "--checkpoint", default="data/analysis/company_analysis_checkpoint.json"
    )
    weekly.add_argument("--model", default="qwen3:8b")
    schedule = subparsers.add_parser(
        "install-weekly", help="install the weekly run in Windows Task Scheduler"
    )
    schedule.add_argument("--dropbox-dir", required=True)
    schedule.add_argument("--day", default="MON")
    schedule.add_argument("--time", default="09:00")
    schedule.add_argument("--task-name", default="HyperVision Company Intelligence Weekly")
    return parser


def apply_url_overrides(companies, path: str):
    override_path = Path(path)
    if not override_path.exists():
        return companies
    payload = json.loads(override_path.read_text(encoding="utf-8"))
    overrides = {name.casefold(): url for name, url in payload.items()}
    return [
        replace(company, official_url=overrides.get(company.name.casefold(), company.official_url))
        for company in companies
    ]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "install-weekly":
        try:
            result = install_windows_weekly_task(
                args.dropbox_dir, Path.cwd(), args.day, args.time, args.task_name
            )
        except (OSError, WeeklyRunError) as exc:
            print(f"schedule installation failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "run-weekly":
        try:
            result = run_weekly_pipeline(
                args.dropbox_dir, args.export_dir, args.database,
                args.checkpoint, args.model,
            )
        except (FileNotFoundError, OSError, PublishError, WeeklyRunError) as exc:
            print(f"weekly run failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "publish":
        try:
            html_path, csv_path = latest_report_pair(args.export_dir)
            result = publish_report_pair(html_path, csv_path, args.dropbox_dir)
        except (FileNotFoundError, OSError, PublishError) as exc:
            print(f"publish failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "report":
        try:
            input_path = Path(args.input) if args.input else latest_analysis(args.export_dir)
            html_path, csv_path, report = export_report(input_path, args.export_dir)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            print(f"report failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({
            "companies": report["company_count"],
            "high_fit": sum(item["fit"] == "high" for item in report["companies"]),
            "medium_fit": sum(item["fit"] == "medium" for item in report["companies"]),
            "html": str(html_path),
            "csv": str(csv_path),
        }, indent=2))
        return 0
    if args.command == "analyze":
        try:
            input_path = Path(args.input) if args.input else latest_evidence(args.export_dir)
            if args.all:
                payload = analyze_all_evidence(
                    input_path, args.checkpoint, model=args.model,
                    endpoint=args.ollama_url, timeout=args.timeout,
                    retries=args.request_retries,
                    refresh_companies=args.refresh_company,
                    progress=lambda index, total, company, status: print(
                        f"[{index}/{total}] {company}: {status}", flush=True
                    ),
                )
                path = export_analysis_batch(payload, args.export_dir)
                print(json.dumps({
                    "companies": payload["company_count"],
                    "completed": payload["completed"],
                    "analyzed": payload["analyzed"],
                    "no_content_available": payload["no_content_available"],
                    "no_verified_facts": payload["no_verified_facts"],
                    "failed": payload["failed"],
                    "commercially_assessed": payload["commercially_assessed"],
                    "checkpoint": payload["checkpoint"],
                    "export": str(path),
                }, indent=2))
                return 0 if payload["failed"] == 0 else 2
            payload = analyze_evidence_file(
                input_path, args.company, model=args.model,
                endpoint=args.ollama_url, timeout=args.timeout,
                retries=args.request_retries,
            )
            path = export_analysis(payload, args.export_dir)
        except KeyboardInterrupt:
            print("\nanalyze interrupted; completed companies remain in the checkpoint")
            return 130
        except (FileNotFoundError, json.JSONDecodeError, AnalysisError) as exc:
            print(f"analyze failed: {exc}", file=sys.stderr)
            return 1
        result = payload["analysis"]
        print(json.dumps({
            "company": result["company"],
            "status": result["analysis_status"],
            "model": result["model"],
            "facts": sum(len(items) for items in result["facts"].values()),
            "commercial_assessment": result.get("commercial_assessment", {}).get(
                "assessment_status", "not_assessed"
            ),
            "base_score": result.get("commercial_assessment", {}).get(
                "customer_partner_scoring", {}
            ).get("base_score", 0),
            "export": str(path),
        }, indent=2))
        return 0
    if args.command == "extract":
        try:
            input_path = Path(args.input) if args.input else latest_crawl(args.export_dir)
            payload = extract_crawl_file(input_path)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            print(f"extract failed: {exc}", file=sys.stderr)
            return 1
        path = export_extraction(payload, args.export_dir)
        print(json.dumps({
            "companies": payload["company_count"],
            "evidence_ready": payload["evidence_ready"],
            "no_content_available": payload["no_content_available"],
            "export": str(path),
        }, indent=2))
        return 0
    if args.command == "audit-robots":
        store = SQLiteStore(args.database)
        store.initialize()
        companies = store.load_latest(args.source_url)
        if not companies:
            print("robots audit failed: no portfolio snapshot; run scan first", file=sys.stderr)
            return 1
        companies = apply_url_overrides(companies, args.url_overrides)
        if args.company:
            requested = {name.casefold() for name in args.company}
            companies = [item for item in companies if item.name.casefold() in requested]
            found = {item.name.casefold() for item in companies}
            missing = sorted(requested - found)
            if missing:
                print(f"robots audit failed: companies not found: {', '.join(missing)}", file=sys.stderr)
                return 1
        inspections = [RobotsInspector().inspect(company) for company in companies]
        json_path, csv_path, states = export_robots_audit(inspections, args.export_dir)
        print(json.dumps({
            "companies": len(inspections),
            "states": states,
            "json": str(json_path),
            "csv": str(csv_path),
        }, indent=2))
        return 0
    if args.command in {"crawl", "audit-access"}:
        store = SQLiteStore(args.database)
        store.initialize()
        companies = store.load_latest(args.source_url)
        if not companies:
            print("crawl failed: no portfolio snapshot; run scan first", file=sys.stderr)
            return 1
        companies = apply_url_overrides(companies, args.url_overrides)
        if args.command == "crawl" and args.company:
            requested = {name.casefold() for name in args.company}
            selected = [company for company in companies if company.name.casefold() in requested]
            missing = sorted(requested - {company.name.casefold() for company in selected})
            if missing:
                print(f"crawl failed: companies not found: {', '.join(missing)}", file=sys.stderr)
                return 1
        else:
            selected = companies[: max(args.limit, 0)] if args.limit else companies
        crawler = CompanySiteCrawler(
            PageFetcher(), args.max_pages, args.max_depth, args.request_delay,
            args.rate_limit_retries, args.rate_limit_backoff,
        )
        crawls = [crawler.crawl(company) for company in selected]
        if args.command == "audit-access":
            json_path, csv_path, categories = export_access_audit(crawls, args.export_dir)
            print(json.dumps({
                "companies": len(crawls),
                "categories": categories,
                "json": str(json_path),
                "csv": str(csv_path),
            }, indent=2))
            return 0
        path = export_crawls(crawls, args.export_dir)
        statuses: dict[str, int] = {}
        for item in crawls:
            statuses[item.status] = statuses.get(item.status, 0) + 1
        print(json.dumps({
            "companies": len(crawls),
            "pages": sum(len(item.pages) for item in crawls),
            "statuses": statuses,
            "export": str(path),
        }, indent=2))
        return 0
    monitor = PortfolioMonitor(PageFetcher(), SQLiteStore(args.database))
    try:
        result = monitor.scan(args.source_name, args.source_url)
    except (FetchError, RuntimeError) as exc:
        print(f"scan failed: {exc}", file=sys.stderr)
        return 1
    paths = export_scan(result, args.export_dir)
    counts: dict[str, int] = {}
    for change in result.changes:
        counts[change.change_type] = counts.get(change.change_type, 0) + 1
    print(
        json.dumps(
            {
                "source": result.source_name,
                "scanned_at": result.scanned_at,
                "companies": len(result.companies),
                "changes": counts,
                "exports": {key: str(path) for key, path in paths.items()},
            },
            indent=2,
        )
    )
    return 0
