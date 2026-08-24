from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

from .models import CompanyRecord


CHECK_PATHS = (
    "/", "/about", "/team", "/leadership", "/products", "/technology",
    "/news", "/press", "/blog", "/careers",
)
ROBOT_TOKEN = "CompanyIntelligenceAgent"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


@dataclass(slots=True)
class RobotsInspection:
    company: str
    site_url: str
    robots_url: str
    http_status: int | None
    final_url: str
    state: str
    crawl_allowed: bool
    crawl_delay: float
    path_permissions: dict[str, bool]
    rules: list[str]
    content: str
    error: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class RobotsInspector:
    def __init__(self, timeout_seconds: float = 30.0) -> None:
        self.timeout_seconds = timeout_seconds

    def inspect(self, company: CompanyRecord) -> RobotsInspection:
        if not company.official_url:
            return RobotsInspection(
                company.name, "", "", None, "", "no_url_provided", False,
                0.0, {}, [], "", "",
            )
        site_url = company.official_url
        parts = urlsplit(site_url)
        robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
        try:
            status, final_url, content = self._fetch(robots_url)
        except HTTPError as exc:
            if 400 <= exc.code < 500:
                return RobotsInspection(
                    company.name, site_url, robots_url, exc.code, robots_url,
                    "robots_unavailable_4xx", True, 0.0,
                    {path: True for path in CHECK_PATHS}, [], "", str(exc),
                )
            return RobotsInspection(
                company.name, site_url, robots_url, exc.code, robots_url,
                "robots_temporarily_unreachable", False, 0.0,
                {path: False for path in CHECK_PATHS}, [], "", str(exc),
            )
        except (URLError, TimeoutError, OSError) as exc:
            return RobotsInspection(
                company.name, site_url, robots_url, None, robots_url,
                "robots_temporarily_unreachable", False, 0.0,
                {path: False for path in CHECK_PATHS}, [], "", str(exc),
            )
        parser = RobotFileParser()
        parser.set_url(final_url)
        parser.parse(content.splitlines())
        permissions = {
            path: parser.can_fetch(ROBOT_TOKEN, urljoin(site_url, path))
            for path in CHECK_PATHS
        }
        allowed_count = sum(permissions.values())
        crawl_delay = parser.crawl_delay(ROBOT_TOKEN) or parser.crawl_delay("*") or 0.0
        state = (
            "allowed" if allowed_count == len(permissions)
            else "full_disallow" if allowed_count == 0
            else "partial_restrictions"
        )
        rules = [
            line.strip() for line in content.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        return RobotsInspection(
            company.name, site_url, robots_url, status, final_url, state,
            permissions.get("/", False), float(crawl_delay), permissions, rules, content, "",
        )

    def _fetch(self, url: str) -> tuple[int, str, str]:
        request = Request(url, headers={
            "User-Agent": "CompanyIntelligenceAgent/0.3 (+https://www.hypervision.ai/)",
            "Accept": "text/plain,*/*;q=0.1",
        })
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = response.read(512_000)
            charset = response.headers.get_content_charset() or "utf-8"
            return response.status, response.geturl(), payload.decode(charset, errors="replace")


def export_robots_audit(
    inspections: list[RobotsInspection], export_dir: str | Path
) -> tuple[Path, Path, dict[str, int]]:
    directory = Path(export_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = utc_stamp()
    json_path = directory / f"robots_audit_{stamp}.json"
    csv_path = directory / f"robots_audit_{stamp}.csv"
    counts: dict[str, int] = {}
    for item in inspections:
        counts[item.state] = counts.get(item.state, 0) + 1
    payload = {
        "company_count": len(inspections),
        "states": counts,
        "companies": [item.to_dict() for item in inspections],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = (
            "company", "site_url", "robots_url", "http_status", "final_url",
            "state", "crawl_allowed", "crawl_delay", "path_permissions", "rules", "error",
        )
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in inspections:
            row = item.to_dict()
            row["path_permissions"] = json.dumps(row["path_permissions"], ensure_ascii=False)
            row["rules"] = " | ".join(item.rules)
            row.pop("content")
            writer.writerow(row)
    return json_path, csv_path, counts
