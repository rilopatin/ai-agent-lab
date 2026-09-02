import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from company_intel.publishing import (
    PublishError, latest_report_pair, publish_report_pair,
)


class PublishingTests(unittest.TestCase):
    def test_first_publish_creates_latest_pair(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            html = root / "company_report_20260101090000.html"
            csv = html.with_suffix(".csv")
            html.write_text("new html", encoding="utf-8")
            csv.write_text("new csv", encoding="utf-8")
            destination = root / "Dropbox"

            result = publish_report_pair(html, csv, destination)

            self.assertEqual((destination / "latest_report.html").read_text(), "new html")
            self.assertEqual((destination / "latest_report.csv").read_text(), "new csv")
            self.assertIsNone(result["archived_html"])

    def test_next_publish_archives_previous_latest_using_its_date(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            destination = root / "Dropbox"
            destination.mkdir()
            old_html = destination / "latest_report.html"
            old_csv = destination / "latest_report.csv"
            old_html.write_text("old html", encoding="utf-8")
            old_csv.write_text("old csv", encoding="utf-8")
            timestamp = datetime(2026, 8, 31, 14, 30).timestamp()
            os.utime(old_html, (timestamp, timestamp))
            os.utime(old_csv, (timestamp, timestamp))
            new_html = root / "company_report_20260907143000.html"
            new_csv = new_html.with_suffix(".csv")
            new_html.write_text("new html", encoding="utf-8")
            new_csv.write_text("new csv", encoding="utf-8")

            publish_report_pair(new_html, new_csv, destination)

            self.assertEqual(
                (destination / "company_report_2026-08-31_14-30-00.html").read_text(),
                "old html",
            )
            self.assertEqual(
                (destination / "company_report_2026-08-31_14-30-00.csv").read_text(),
                "old csv",
            )
            self.assertEqual((destination / "latest_report.html").read_text(), "new html")
            self.assertEqual((destination / "latest_report.csv").read_text(), "new csv")

    def test_refuses_incomplete_latest_pair(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source_html = root / "company_report_1.html"
            source_csv = root / "company_report_1.csv"
            source_html.write_text("html")
            source_csv.write_text("csv")
            destination = root / "Dropbox"
            destination.mkdir()
            (destination / "latest_report.html").write_text("orphan")
            with self.assertRaises(PublishError):
                publish_report_pair(source_html, source_csv, destination)

    def test_finds_latest_complete_report_pair(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / "company_report_1.html").write_text("old")
            (root / "company_report_1.csv").write_text("old")
            (root / "company_report_2.html").write_text("incomplete")
            self.assertEqual(latest_report_pair(root)[0].name, "company_report_1.html")
