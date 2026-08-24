from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from company_intel.extraction import extract_company, extract_crawl_file, latest_crawl


class ExtractionTests(unittest.TestCase):
    def test_extracts_contacts_and_sourced_evidence(self) -> None:
        profile = extract_company({
            "company": "Example",
            "start_url": "https://example.com/",
            "status": "ok",
            "pages": [{
                "url": "https://example.com/about",
                "title": "About",
                "text": (
                    "Jane Doe is the founder and CEO. "
                    "Our autonomous camera platform supports mapping missions. "
                    "Contact sales@example.com."
                ),
            }],
        })
        self.assertEqual(profile["extraction_status"], "evidence_ready")
        self.assertEqual(profile["contacts"]["emails"], ["sales@example.com"])
        self.assertEqual(
            profile["evidence"]["leadership"][0]["source_url"],
            "https://example.com/about",
        )
        self.assertTrue(profile["evidence"]["technology"])

    def test_preserves_company_without_pages(self) -> None:
        profile = extract_company({
            "company": "No Site", "start_url": "", "status": "no_url_provided", "pages": []
        })
        self.assertEqual(profile["extraction_status"], "no_content_available")

    def test_treats_placeholder_as_no_content(self) -> None:
        profile = extract_company({
            "company": "Coming Soon", "start_url": "https://example.com",
            "status": "placeholder_no_content",
            "pages": [{"url": "https://example.com", "text": "Coming soon. Check back soon."}],
        })
        self.assertEqual(profile["extraction_status"], "no_content_available")
        self.assertEqual(profile["source_page_count"], 0)

    def test_filters_service_pages_and_false_funding_matches(self) -> None:
        profile = extract_company({
            "company": "Example", "start_url": "https://example.com", "status": "ok",
            "pages": [
                {
                    "url": "https://example.com/privacy-policy",
                    "title": "Privacy Policy",
                    "text": "We perform contracts and protect your investment information.",
                },
                {
                    "url": "https://example.com/blog",
                    "title": "Blog",
                    "text": (
                        "This is part one of a three-part series by our CEO. "
                        "The company raised $5 million in a seed round. "
                        "Our Chief Operating Officer spoke at the event. "
                        "Every pixel is precisely located in 3D space."
                    ),
                },
            ],
        })
        self.assertEqual(len(profile["evidence"]["funding"]), 1)
        self.assertIn("raised $5 million", profile["evidence"]["funding"][0]["snippet"])
        self.assertEqual(profile["evidence"]["locations"], [])

    def test_uses_latest_crawl_and_reports_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "company_sites_20260101000000.json").write_text(
                json.dumps({"companies": []}), encoding="utf-8"
            )
            latest = root / "company_sites_20260102000000.json"
            latest.write_text(json.dumps({"companies": [{
                "company": "Example", "start_url": "https://example.com",
                "status": "ok", "pages": [{"url": "https://example.com", "text": "Product"}],
            }]}), encoding="utf-8")
            self.assertEqual(latest_crawl(root), latest)
            payload = extract_crawl_file(latest)
            self.assertEqual(payload["company_count"], 1)
            self.assertEqual(payload["evidence_ready"], 1)


if __name__ == "__main__":
    unittest.main()
