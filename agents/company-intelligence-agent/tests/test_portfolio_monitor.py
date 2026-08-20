from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from company_intel.diffing import compare_companies
from company_intel.models import ScanResult
from company_intel.parsers import parse_genius_ny_portfolio
from company_intel.storage import SQLiteStore


FIXTURES = Path(__file__).parent / "fixtures"
SOURCE_URL = "https://geniusny.com/portfolio/"


class PortfolioParserTests(unittest.TestCase):
    def test_extracts_companies_links_descriptions_and_aliases(self) -> None:
        companies = parse_genius_ny_portfolio(
            (FIXTURES / "genius_portfolio_v1.html").read_text(), SOURCE_URL
        )
        self.assertEqual([item.name for item in companies], ["Circle Optics", "Dexory", "Wonder Robotics"])
        self.assertEqual(companies[0].official_url, "https://circleoptics.com/")
        self.assertIn("Parallax-Free", companies[0].description or "")
        self.assertEqual(companies[1].aliases, ("Bots and Us",))
        self.assertNotIn("formerly", companies[1].description or "")

    def test_detects_added_removed_and_changed(self) -> None:
        before = parse_genius_ny_portfolio(
            (FIXTURES / "genius_portfolio_v1.html").read_text(), SOURCE_URL
        )
        after = parse_genius_ny_portfolio(
            (FIXTURES / "genius_portfolio_v2.html").read_text(), SOURCE_URL
        )
        changes = compare_companies(before, after)
        by_type = {kind: [item for item in changes if item.change_type == kind] for kind in {c.change_type for c in changes}}
        self.assertEqual(len(by_type["added"]), 1)
        self.assertEqual(by_type["added"][0].after.name, "New Drone Company")
        self.assertEqual(len(by_type["removed"]), 1)
        self.assertEqual(by_type["removed"][0].before.name, "Wonder Robotics")
        self.assertEqual(len(by_type["changed"]), 1)
        self.assertEqual(by_type["changed"][0].after.name, "Circle Optics")


class SQLiteStoreTests(unittest.TestCase):
    def test_round_trip_latest_snapshot(self) -> None:
        companies = parse_genius_ny_portfolio(
            (FIXTURES / "genius_portfolio_v1.html").read_text(), SOURCE_URL
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "agent.db")
            store.initialize()
            store.save(ScanResult("GENIUS NY", SOURCE_URL, companies=companies))
            loaded = store.load_latest(SOURCE_URL)
        self.assertEqual(loaded, sorted(companies, key=lambda item: item.name.casefold()))


if __name__ == "__main__":
    unittest.main()
