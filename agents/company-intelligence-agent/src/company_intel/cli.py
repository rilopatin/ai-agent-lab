from __future__ import annotations

import argparse
import json
import sys

from .exporting import export_scan
from .fetch import FetchError, PageFetcher
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "scan":
        return 2
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

