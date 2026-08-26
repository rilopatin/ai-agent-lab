import csv
import json
import tempfile
import unittest
from pathlib import Path

from company_intel.reporting import build_report, export_report, score_company


class ReportingTests(unittest.TestCase):
    def test_uses_structured_assessment_and_never_keyword_scores(self):
        relevant = {
            "company": "Vision Co", "analysis_status": "analyzed",
            "summary": "A 360 camera and computer vision platform for drone threat detection in defense.",
            "facts": {"technology": [{"statement": "Uses LiDAR sensor integration."}]},
            "commercial_assessment": {
                "assessment_status": "assessed",
                "customer_partner_scoring": {
                    "human_perception_need": 2, "integration_fit": 2,
                    "commercial_capacity": 1, "timing_and_access": 0,
                    "base_score": 5,
                },
                "geography": {"eligibility": "eligible", "affinity_modifier": 1},
            },
        }
        unavailable = {
            "company": "Missing Co", "analysis_status": "no_content_available",
            "summary": "drone camera defense", "facts": {},
        }
        self.assertEqual(score_company(relevant)["score"], 5)
        self.assertEqual(score_company(relevant)["fit"], "medium")
        self.assertEqual(score_company(relevant)["modifier"], 1)
        self.assertEqual(score_company(unavailable)["score"], 0)
        self.assertEqual(score_company(unavailable)["fit"], "not_scored")

    def test_report_sorts_by_score_and_exports_readable_html_and_csv(self):
        payload = {"analyses": [{
            "company": "Low Co", "website": "https://low.example",
            "analysis_status": "analyzed", "summary": "Makes delivery software.",
            "facts": {}, "commercial_assessment": {
                "assessment_status": "assessed",
                "customer_partner_scoring": {"base_score": 1},
                "geography": {"eligibility": "eligible", "affinity_modifier": 0},
            },
        }, {
            "company": "High Co", "website": "https://high.example",
            "analysis_status": "analyzed", "summary": "Defense drone camera threat detection.",
            "facts": {"technology": [{
                "statement": "Computer vision sensor platform.",
                "source_url": "https://high.example/technology",
            }]}, "commercial_assessment": {
                "assessment_status": "assessed",
                "relationship_types": ["technology_partner"],
                "relationship_hypothesis": "Potential integration partner.",
                "hypervision_relevance": "Provides a complementary platform.",
                "first_engagement": "Technical workshop.",
                "confidence": "medium",
                "customer_partner_scoring": {"base_score": 8},
                "potential_score": 10,
                "verification_status": "qualified",
                "verification_questions": ["Who owns the pilot budget?"],
                "geography": {"eligibility": "eligible", "affinity_modifier": 0},
            },
        }]}
        report = build_report(payload)
        self.assertEqual(report["companies"][0]["company"], "High Co")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "analysis.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            html_path, csv_path, _ = export_report(source, directory)
            html_text = html_path.read_text(encoding="utf-8")
            with csv_path.open(encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
        self.assertIn("HyperVision Company Intelligence", html_text)
        self.assertIn("https://high.example/technology", html_text)
        self.assertEqual(rows[0]["company"], "High Co")
        self.assertEqual(rows[0]["relationship_types"], "technology_partner")
        self.assertEqual(rows[0]["confirmed_score"], "8")
        self.assertEqual(rows[0]["potential_score"], "10")
        self.assertEqual(rows[0]["verification_status"], "qualified")

    def test_excludes_verified_russian_company_even_with_high_base_score(self):
        analysis = {
            "analysis_status": "analyzed",
            "commercial_assessment": {
                "assessment_status": "assessed",
                "customer_partner_scoring": {"base_score": 10},
                "geography": {
                    "eligibility": "excluded_geography", "affinity_modifier": 0,
                },
            },
        }
        result = score_company(analysis)
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["fit"], "excluded")
