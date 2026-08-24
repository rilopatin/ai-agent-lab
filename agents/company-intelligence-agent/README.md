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

Without `--limit`, `crawl` processes every company in the latest portfolio
snapshot, including companies added by later scans.

Prepare compact, source-linked evidence from the latest crawl without calling
an LLM:

```bash
python -m company_intel extract
```

The evidence export keeps companies without usable pages as
`no_content_available` instead of treating them as extraction failures.

Specific companies can be selected by repeating `--company`:

```bash
python -m company_intel crawl --company "Circle Optics" --company "Wonder Robotics"
```

The crawler stays on each company's domain, respects robots.txt, prioritizes
About/Team/Technology/Product/News pages, and applies page and depth limits.
Verified replacements for stale portfolio URLs are kept in
`config/company_url_overrides.json`. HTTP 429 responses receive a small,
bounded exponential-backoff retry sequence; exhausted retries are reported as
`automation_rate_limited`, while already collected pages are preserved.
If the bounded retries still receive HTTP 429, the crawler opens the installed
Google Chrome through Playwright and, when it succeeds, uses that mode for the
rest of the current company. It does not solve CAPTCHAs or bypass robots.txt.
Install the optional browser dependency with `python -m pip install playwright`;
the fallback uses the existing stable Chrome installation.

Before a full crawl, audit access to every company site:

```bash
python -m company_intel audit-access
```

The audit makes a shallow three-page check and exports both JSON and CSV. It
separates accessible sites from robots.txt restrictions, HTTP 429 rate limits,
HTTP 401/403 denials, browser/anti-bot challenge pages, URLs not provided by
the source, and unavailable sites. A missing URL or unavailable site is recorded
as a completed check whose direct crawl was skipped, not as an agent failure.

Inspect the actual robots.txt response and permissions separately:

```bash
python -m company_intel audit-robots
```

The JSON report preserves the exact file contents and tests common public paths
such as About, Team, Products, Technology, News, Blog, and Careers. HTTP 4xx
responses are distinguished from temporary server/network failures according
to RFC 9309 instead of being treated as an explicit full-site prohibition.
The crawler uses the same inspection result, follows only the applicable path
rules, and raises its request interval when a site publishes a crawl delay.
Successful Chrome fallback runs are reported as `ok_browser_fallback`. Common
placeholder, site-not-found, and critical-error landing pages receive distinct
statuses instead of being treated as useful company content.
Rendered HTTP 4xx/5xx landing pages are inspected before classification, and
the crawler uses the final response URL after redirects when resolving internal
links.

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
- Full-portfolio website access audit with failure-cause classification
- Detailed robots.txt audit with per-path permission checks

Next: company-site crawling, evidence extraction, HyperVision-fit analysis,
news monitoring, and email digests.
