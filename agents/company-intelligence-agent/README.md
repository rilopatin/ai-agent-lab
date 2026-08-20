# Company Intelligence Agent

Persistent company and portfolio monitoring for HyperVision.

The first implemented slice monitors the GENIUS NY portfolio. Every scan
rebuilds the current company list, compares it with the previous snapshot,
keeps historical records, and reports added, removed, and changed entries.

## Run

```bash
python -m company_intel scan \
  --source-url https://geniusny.com/portfolio/ \
  --database data/company_intelligence.db \
  --export-dir data/exports
```

The command writes a timestamped JSON snapshot, a CSV export, and a JSON
change report. On the first run, every discovered company is reported as new.

## Tests

```bash
python -m unittest discover -s tests -v
```

## Current scope

- Dynamic portfolio discovery on every run
- Persistent SQLite history
- Added/removed/changed detection
- Company-name and URL normalization
- Rename hints based on stable domains and previous-name labels
- JSON and CSV exports

Next: company-site crawling, evidence extraction, HyperVision-fit analysis,
news monitoring, and email digests.

