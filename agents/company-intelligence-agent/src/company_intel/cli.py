from __future__ import annotations

import argparse
import json
import sys

from .exporting import export_scan
from .fetch import FetchError, PageFetcher
from .crawling import CompanySiteCrawler, export_crawls
from .monitor import PortfolioMonitor
from .storage import SQLiteStore


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
    crawl.add_argument("--limit", type=int, default=3)
    crawl.add_argument("--max-pages", type=int, default=15)
    crawl.add_argument("--max-depth", type=int, default=2)
    crawl.add_argument("--request-delay", type=float, default=1.0)
    crawl.add_argument(
        "--company", action="append", default=[],
        help="crawl a named company; may be supplied more than once",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "crawl":
        store = SQLiteStore(args.database)
        store.initialize()
        companies = store.load_latest(args.source_url)
        if not companies:
            print("crawl failed: no portfolio snapshot; run scan first", file=sys.stderr)
            return 1
        if args.company:
            requested = {name.casefold() for name in args.company}
            selected = [company for company in companies if company.name.casefold() in requested]
            missing = sorted(requested - {company.name.casefold() for company in selected})
            if missing:
                print(f"crawl failed: companies not found: {', '.join(missing)}", file=sys.stderr)
                return 1
        else:
            selected = companies[: max(args.limit, 0)] if args.limit else companies
        crawler = CompanySiteCrawler(
            PageFetcher(), args.max_pages, args.max_depth, args.request_delay
        )
        crawls = [crawler.crawl(company) for company in selected]
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
