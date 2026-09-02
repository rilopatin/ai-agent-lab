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
### Analyze one company locally with Ollama

Commercial analysis is governed by
`config/hypervision_decision_profile.json`. The local model first extracts
source-linked company facts and then applies the decision profile to classify
the possible relationship, score customer/partner and investor fit separately,
apply geography eligibility, identify integration dependencies, and propose an
evidence-supported first commercial step.

The report does not award relevance points for keywords such as drones,
defense, cameras, sensors, or LiDAR. Scores come only from the structured,
source-grounded commercial assessment. Changes to the analysis methodology
invalidate older checkpoints automatically, so the next `analyze --all` run
recalculates every company under the current rules.

The assessment separates the evidence-backed confirmed score from a conditional
potential score. Missing public information is not treated as proof of absence.
Each company receives a verification status and concrete questions showing what
must be checked before the potential score can become a confirmed score.

After `extract`, run a source-grounded analysis with the local Qwen model:

```cmd
set PYTHONPATH=src && python -m company_intel analyze --company "Circle Optics"
```

The command sends only selected evidence to `http://localhost:11434`, requires
structured JSON, and removes facts whose source URL was not supplied to the model.
Evidence is split into small thematic groups so the local model stays within its
4096-token context instead of dropping information from a long company profile.
Each accepted fact must also contain a verbatim quote that the program can find
inside the supplied source snippet. The final summary is generated once, using
only facts that passed these checks.
Before the model runs, deterministic relevance rules remove third-party
leadership mentions, generic industry news, and generic technology statements.
Duplicate statements are retained in only one category.
The program also verifies that a quote was supplied in the same thematic batch
and that the statement's meaningful words are substantially supported by it.
Research questions and mission wording are not treated as delivered products.
An official page title that includes the company name can establish relevance
even when the page text itself uses only a product name such as Argonaut.
Location evidence is accepted only when the target company name appears close
to the location phrase, preventing a partner's country from becoming the
company's location. Local model requests allow up to 15 minutes by default.

Analyze the entire evidence file with a resumable checkpoint:

```cmd
set PYTHONPATH=src && python -m company_intel analyze --all
```

Progress is saved after every company in `data/analysis`. Running the same
command again skips completed companies and retries failed ones. Each local
request is retried once by default, and Ctrl+C preserves completed work.

### Create the final HyperVision report

Create a ranked HTML report for reading and a UTF-8 CSV for Excel from the
latest complete analysis:

```cmd
set PYTHONPATH=src && python -m company_intel report
```

The relevance score is deterministic and explainable. It awards independent
signals for imaging/computer vision, defense and security use cases, aerial
autonomy, relevant sensors, detection/tracking, and integration potential.
Companies without verified analysis remain visible but are not scored.

### Publish reports to Dropbox

The agent publishes to a normal Dropbox-synced local folder; no Dropbox API or
cloud credentials are required. Publish the newest complete HTML/CSV pair with:

```cmd
set PYTHONPATH=src && python -m company_intel publish --dropbox-dir "C:\path\to\Dropbox\HyperVision\Company Intelligence"
```

The folder always contains `latest_report.html` and `latest_report.csv`. Before
they are replaced, the previous pair is archived in the same folder under its
creation date, for example `company_report_2026-09-01_09-00-00.html` and `.csv`.
Publishing is atomic and refuses to replace an incomplete latest pair.

Run the complete workflow (scan, crawl, extract, analyze, report and publish)
with one command:

```cmd
set PYTHONPATH=src && python -m company_intel run-weekly --dropbox-dir "C:\path\to\Dropbox\HyperVision\Company Intelligence"
```

If a stage fails, the workflow stops and the existing Dropbox report is left
unchanged.

Install the workflow as a weekly Windows task (Monday at 09:00 by default):

```cmd
set PYTHONPATH=src && python -m company_intel install-weekly --dropbox-dir "C:\path\to\Dropbox\HyperVision\Company Intelligence"
```

The frequency is currently fixed to weekly. The day and time can be selected
during installation with `--day` and `--time`. The generated runner writes its
output to `data\weekly_report.log`. Windows is configured to start a missed run
when the laptop becomes available and to allow the task to continue on battery.
