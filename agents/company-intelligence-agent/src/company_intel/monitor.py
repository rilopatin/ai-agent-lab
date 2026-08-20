from __future__ import annotations

from .diffing import compare_companies
from .fetch import PageFetcher
from .models import ScanResult
from .parsers import parse_genius_ny_portfolio
from .storage import SQLiteStore


class PortfolioMonitor:
    def __init__(self, fetcher: PageFetcher, store: SQLiteStore) -> None:
        self.fetcher = fetcher
        self.store = store

    def scan(self, source_name: str, source_url: str) -> ScanResult:
        self.store.initialize()
        previous = self.store.load_latest(source_url)
        html = self.fetcher.get_text(source_url)
        current = parse_genius_ny_portfolio(html, source_url)
        if not current:
            raise RuntimeError(
                "The portfolio parser found no companies; refusing to save an empty snapshot."
            )
        changes = compare_companies(previous, current)
        result = ScanResult(
            source_name=source_name,
            source_url=source_url,
            companies=current,
            changes=changes,
        )
        self.store.save(result)
        return result

