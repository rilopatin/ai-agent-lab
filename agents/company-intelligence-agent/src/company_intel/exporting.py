from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from .models import ScanResult


def export_scan(result: ScanResult, export_dir: str | Path) -> dict[str, Path]:
    destination = Path(export_dir)
    destination.mkdir(parents=True, exist_ok=True)
    stamp = re.sub(r"[^0-9]", "", result.scanned_at)[:14]
    base = f"genius_ny_{stamp}"
    snapshot_path = destination / f"{base}_snapshot.json"
    changes_path = destination / f"{base}_changes.json"
    csv_path = destination / f"{base}_companies.csv"

    snapshot_path.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    changes_path.write_text(
        json.dumps([change.to_dict() for change in result.changes], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["name", "official_url", "description", "aliases", "portfolio_url"],
        )
        writer.writeheader()
        for company in result.companies:
            writer.writerow(
                {
                    "name": company.name,
                    "official_url": company.official_url or "",
                    "description": company.description or "",
                    "aliases": " | ".join(company.aliases),
                    "portfolio_url": company.portfolio_url,
                }
            )
    return {"snapshot": snapshot_path, "changes": changes_path, "csv": csv_path}

