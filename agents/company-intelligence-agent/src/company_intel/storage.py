from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .models import CompanyChange, CompanyRecord, ScanResult


class SQLiteStore:
    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_name TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    scanned_at TEXT NOT NULL,
                    company_count INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scan_companies (
                    scan_id INTEGER NOT NULL REFERENCES scans(id),
                    company_key TEXT NOT NULL,
                    name TEXT NOT NULL,
                    official_url TEXT,
                    description TEXT,
                    aliases_json TEXT NOT NULL,
                    portfolio_url TEXT NOT NULL,
                    PRIMARY KEY (scan_id, company_key)
                );
                CREATE TABLE IF NOT EXISTS changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_id INTEGER NOT NULL REFERENCES scans(id),
                    change_type TEXT NOT NULL,
                    company_key TEXT NOT NULL,
                    before_json TEXT,
                    after_json TEXT,
                    changed_fields_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_scans_source
                    ON scans(source_url, id DESC);
                CREATE INDEX IF NOT EXISTS idx_changes_scan
                    ON changes(scan_id);
                """
            )

    def load_latest(self, source_url: str) -> list[CompanyRecord]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM scans WHERE source_url = ? ORDER BY id DESC LIMIT 1",
                (source_url,),
            ).fetchone()
            if row is None:
                return []
            company_rows = connection.execute(
                """
                SELECT name, portfolio_url, official_url, description, aliases_json
                FROM scan_companies WHERE scan_id = ? ORDER BY name COLLATE NOCASE
                """,
                (row["id"],),
            ).fetchall()
        return [
            CompanyRecord(
                name=item["name"],
                portfolio_url=item["portfolio_url"],
                official_url=item["official_url"],
                description=item["description"],
                aliases=tuple(json.loads(item["aliases_json"])),
            )
            for item in company_rows
        ]

    def save(self, result: ScanResult) -> int:
        from .identity import company_key

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO scans(source_name, source_url, scanned_at, company_count)
                VALUES (?, ?, ?, ?)
                """,
                (result.source_name, result.source_url, result.scanned_at, len(result.companies)),
            )
            scan_id = int(cursor.lastrowid)
            connection.executemany(
                """
                INSERT INTO scan_companies(
                    scan_id, company_key, name, official_url, description,
                    aliases_json, portfolio_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        scan_id,
                        company_key(company.name, company.official_url),
                        company.name,
                        company.official_url,
                        company.description,
                        json.dumps(list(company.aliases), ensure_ascii=False),
                        company.portfolio_url,
                    )
                    for company in result.companies
                ],
            )
            connection.executemany(
                """
                INSERT INTO changes(
                    scan_id, change_type, company_key, before_json,
                    after_json, changed_fields_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [self._change_row(scan_id, change) for change in result.changes],
            )
            return scan_id

    @staticmethod
    def _change_row(scan_id: int, change: CompanyChange) -> tuple[object, ...]:
        return (
            scan_id,
            change.change_type,
            change.company_key,
            json.dumps(change.before.to_dict(), ensure_ascii=False) if change.before else None,
            json.dumps(change.after.to_dict(), ensure_ascii=False) if change.after else None,
            json.dumps(list(change.changed_fields)),
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()
