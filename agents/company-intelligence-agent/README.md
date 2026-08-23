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

To test company-site crawling on the first three companies from the latest scan:

```bash
python -m company_intel crawl --limit 3
```

Specific companies can be selected by repeating `--company`:

```bash
python -m company_intel crawl --company "Circle Optics" --company "Wonder Robotics"
```

The crawler stays on each company's domain, respects robots.txt, prioritizes
About/Team/Technology/Product/News pages, and applies page and depth limits.

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
- Bounded, same-domain company-site crawling with robots.txt support

Next: company-site crawling, evidence extraction, HyperVision-fit analysis,
news monitoring, and email digests.
