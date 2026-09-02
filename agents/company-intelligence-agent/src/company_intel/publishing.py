from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path


class PublishError(RuntimeError):
    pass


def latest_report_pair(export_dir: str | Path) -> tuple[Path, Path]:
    directory = Path(export_dir)
    html_candidates = sorted(directory.glob("company_report_*.html"))
    for html_path in reversed(html_candidates):
        csv_path = html_path.with_suffix(".csv")
        if csv_path.exists():
            return html_path, csv_path
    raise FileNotFoundError(
        "no matching company_report_*.html and .csv files found; run report first"
    )


def _archive_stamp(path: Path) -> str:
    created = datetime.fromtimestamp(path.stat().st_mtime)
    return created.strftime("%Y-%m-%d_%H-%M-%S")


def _available_archive_pair(directory: Path, stamp: str) -> tuple[Path, Path]:
    suffix = 1
    while True:
        extra = "" if suffix == 1 else f"_{suffix}"
        stem = f"company_report_{stamp}{extra}"
        html_path = directory / f"{stem}.html"
        csv_path = directory / f"{stem}.csv"
        if not html_path.exists() and not csv_path.exists():
            return html_path, csv_path
        suffix += 1


def _atomic_copy(source: Path, destination: Path) -> None:
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def publish_report_pair(
    html_source: str | Path,
    csv_source: str | Path,
    destination_dir: str | Path,
) -> dict[str, str | None]:
    html_source = Path(html_source)
    csv_source = Path(csv_source)
    if not html_source.is_file() or not csv_source.is_file():
        raise PublishError("both HTML and CSV report files are required")

    destination = Path(destination_dir).expanduser()
    destination.mkdir(parents=True, exist_ok=True)
    latest_html = destination / "latest_report.html"
    latest_csv = destination / "latest_report.csv"

    if latest_html.exists() != latest_csv.exists():
        raise PublishError(
            "Dropbox destination contains only one latest report file; "
            "restore or remove the incomplete pair before publishing"
        )

    archived_html: Path | None = None
    archived_csv: Path | None = None
    if latest_html.exists():
        stamp = _archive_stamp(latest_html)
        archived_html, archived_csv = _available_archive_pair(destination, stamp)
        os.replace(latest_html, archived_html)
        try:
            os.replace(latest_csv, archived_csv)
        except OSError:
            os.replace(archived_html, latest_html)
            raise

    try:
        _atomic_copy(html_source, latest_html)
        _atomic_copy(csv_source, latest_csv)
    except OSError:
        latest_html.unlink(missing_ok=True)
        latest_csv.unlink(missing_ok=True)
        if archived_html and archived_csv:
            os.replace(archived_html, latest_html)
            os.replace(archived_csv, latest_csv)
        raise

    return {
        "latest_html": str(latest_html),
        "latest_csv": str(latest_csv),
        "archived_html": str(archived_html) if archived_html else None,
        "archived_csv": str(archived_csv) if archived_csv else None,
    }
