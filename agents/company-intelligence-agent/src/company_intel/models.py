from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True, slots=True)
class CompanyRecord:
    name: str
    portfolio_url: str
    official_url: str | None = None
    description: str | None = None
    aliases: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["aliases"] = list(self.aliases)
        return data


@dataclass(frozen=True, slots=True)
class CompanyChange:
    change_type: str
    company_key: str
    before: CompanyRecord | None = None
    after: CompanyRecord | None = None
    changed_fields: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_type": self.change_type,
            "company_key": self.company_key,
            "before": self.before.to_dict() if self.before else None,
            "after": self.after.to_dict() if self.after else None,
            "changed_fields": list(self.changed_fields),
        }


@dataclass(slots=True)
class ScanResult:
    source_name: str
    source_url: str
    scanned_at: str = field(default_factory=utc_now_iso)
    companies: list[CompanyRecord] = field(default_factory=list)
    changes: list[CompanyChange] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "source_url": self.source_url,
            "scanned_at": self.scanned_at,
            "company_count": len(self.companies),
            "change_count": len(self.changes),
            "companies": [company.to_dict() for company in self.companies],
            "changes": [change.to_dict() for change in self.changes],
        }

