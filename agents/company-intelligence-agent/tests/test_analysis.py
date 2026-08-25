import json
import tempfile
import unittest
from pathlib import Path

from company_intel.analysis import AnalysisError, analyze_evidence_file, analyze_profile


class LocalAnalysisTests(unittest.TestCase):
    def test_filters_third_party_leadership_and_generic_news_before_model(self):
        profile = {
            "company": "Circle Optics",
            "website": "https://circleoptics.com",
            "extraction_status": "evidence_ready",
            "contacts": {"emails": []},
            "evidence": {
                "leadership": [
                    {
                        "source_url": "https://circleoptics.com/blog/",
                        "snippet": "Michael Robbins leads AUVSI with a focus on service.",
                    },
                    {
                        "source_url": "https://circleoptics.com/blog/",
                        "snippet": "Circle Optics promoted Grace Annese to Senior Software Engineer.",
                    },
                    {
                        "source_url": "https://circleoptics.com/team/",
                        "snippet": "Zak and Ian provide inspirational leadership.",
                    },
                ],
                "news": [
                    {
                        "source_url": "https://circleoptics.com/blog/",
                        "snippet": "The regional technology awards recognize local talent.",
                    },
                    {
                        "source_url": "https://circleoptics.com/circle-technology/",
                        "snippet": "Circle Optics is working under contract with NASA and NSF.",
                    },
                ],
            },
        }
        prompts = []

        def fake_transport(endpoint, payload, timeout):
            if "facts" not in payload["format"]["properties"]:
                return {"message": {"content": '{"summary":""}'}}
            prompts.append(payload["messages"][1]["content"])
            facts = {
                category: []
                for category in payload["format"]["properties"]["facts"]["required"]
            }
            return {"message": {"content": json.dumps({"summary": "", "facts": facts})}}

        analyze_profile(profile, transport=fake_transport)
        combined = " ".join(prompts)
        self.assertNotIn("Michael Robbins", combined)
        self.assertNotIn("regional technology awards", combined)
        self.assertIn("Grace Annese", combined)
        self.assertIn("Zak and Ian", combined)
        self.assertIn("NASA and NSF", combined)

    def test_keeps_official_product_technology_but_drops_research_questions(self):
        profile = {
            "company": "Archangel Autonomy",
            "website": "https://www.archangelautonomy.com",
            "extraction_status": "evidence_ready",
            "contacts": {"emails": []},
            "evidence": {
                "products": [{
                    "source_url": "https://www.archangelautonomy.com/company",
                    "source_title": "Archangel Autonomy",
                    "snippet": "How do we enable a small UAS to navigate accurately?",
                }],
                "technology": [{
                    "source_url": "https://www.archangelautonomy.com/argonaut",
                    "source_title": "Argonaut | Archangel Autonomy",
                    "snippet": "Self-contained autonomous sensors provide immediate protection anywhere.",
                }],
            },
        }
        prompts = []

        def fake_transport(endpoint, payload, timeout):
            if "facts" not in payload["format"]["properties"]:
                return {"message": {"content": '{"summary":""}'}}
            prompts.append(payload["messages"][1]["content"])
            facts = {category: [] for category in payload["format"]["properties"]["facts"]["required"]}
            return {"message": {"content": json.dumps({"summary": "", "facts": facts})}}

        analyze_profile(profile, transport=fake_transport)
        combined = " ".join(prompts)
        self.assertNotIn("How do we enable", combined)
        self.assertIn("Self-contained autonomous sensors", combined)

    def test_location_must_describe_target_company_not_its_partner(self):
        profile = {
            "company": "Fotokite",
            "website": "https://fotokite.com",
            "extraction_status": "evidence_ready",
            "contacts": {"emails": []},
            "evidence": {"locations": [
                {
                    "source_url": "https://fotokite.com/news",
                    "source_title": "News | Fotokite",
                    "snippet": (
                        "Fotokite partners with PSTR Group, a provider of emergency "
                        "technology solutions based in Australia."
                    ),
                },
                {
                    "source_url": "https://fotokite.com/about",
                    "source_title": "About | Fotokite",
                    "snippet": "Fotokite is headquartered in Zurich, Switzerland.",
                },
            ]},
        }
        prompts = []

        def fake_transport(endpoint, payload, timeout):
            if "facts" not in payload["format"]["properties"]:
                return {"message": {"content": '{"summary":""}'}}
            prompts.append(payload["messages"][1]["content"])
            facts = {category: [] for category in payload["format"]["properties"]["facts"]["required"]}
            return {"message": {"content": json.dumps({"summary": "", "facts": facts})}}

        analyze_profile(profile, transport=fake_transport)
        combined = " ".join(prompts)
        self.assertNotIn("based in Australia", combined)
        self.assertIn("headquartered in Zurich", combined)

    def test_analyzes_only_source_linked_facts_and_disables_thinking(self):
        profile = {
            "company": "Circle Optics",
            "website": "https://circleoptics.com",
            "extraction_status": "evidence_ready",
            "contacts": {"emails": []},
            "evidence": {
                "technology": [{
                    "source_url": "https://circleoptics.com/technology",
                    "snippet": "Circle Optics develops panoramic imaging technology.",
                }],
            },
        }

        calls = []

        def fake_transport(endpoint, payload, timeout):
            calls.append(payload)
            self.assertEqual(endpoint, "http://localhost:11434/api/chat")
            self.assertFalse(payload["think"])
            self.assertEqual(payload["format"]["type"], "object")
            if "facts" not in payload["format"]["properties"]:
                return {"message": {"content": json.dumps({
                    "summary": "Circle Optics develops panoramic imaging technology."
                })}}
            facts = {category: [] for category in payload["format"]["properties"]["facts"]["required"]}
            evidence_text = payload["messages"][1]["content"]
            facts["technology"] = [
                {
                    "statement": "It develops panoramic imaging technology.",
                    "source_url": "https://circleoptics.com/technology",
                    "evidence_quote": "develops panoramic imaging technology",
                    "confidence": "high",
                },
                {
                    "statement": "Unsupported claim.",
                    "source_url": "https://invented.example/source",
                    "evidence_quote": "Unsupported claim",
                    "confidence": "high",
                },
            ] if "panoramic imaging" in evidence_text else []
            return {"message": {"content": json.dumps({"summary": "Imaging company.", "facts": facts})}}

        result = analyze_profile(profile, transport=fake_transport)
        self.assertEqual(result["analysis_status"], "analyzed")
        self.assertEqual(len(result["facts"]["technology"]), 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["options"]["num_ctx"], 4096)
        self.assertEqual(
            result["summary"],
            "Circle Optics develops panoramic imaging technology.",
        )

    def test_rejects_quote_not_present_in_source_snippet(self):
        profile = {
            "company": "Circle Optics",
            "website": "https://circleoptics.com",
            "extraction_status": "evidence_ready",
            "contacts": {"emails": []},
            "evidence": {"technology": [{
                "source_url": "https://circleoptics.com/technology",
                "snippet": "The company makes panoramic cameras.",
            }]},
        }

        def fake_transport(endpoint, payload, timeout):
            if "facts" not in payload["format"]["properties"]:
                return {"message": {"content": '{"summary":""}'}}
            facts = {category: [] for category in payload["format"]["properties"]["facts"]["required"]}
            facts["technology"] = [{
                "statement": "The camera uses quantum sensors.",
                "source_url": "https://circleoptics.com/technology",
                "evidence_quote": "The camera uses quantum sensors.",
                "confidence": "high",
            }]
            return {"message": {"content": json.dumps({"summary": "", "facts": facts})}}

        result = analyze_profile(profile, transport=fake_transport)
        self.assertEqual(result["facts"]["technology"], [])

    def test_rejects_statement_not_supported_by_its_real_quote(self):
        profile = {
            "company": "Circle Optics",
            "website": "https://circleoptics.com",
            "extraction_status": "evidence_ready",
            "contacts": {"emails": []},
            "evidence": {"technology": [{
                "source_url": "https://circleoptics.com/technology",
                "snippet": "Circle Optics enables accurate three dimensional mapping.",
            }]},
        }

        def fake_transport(endpoint, payload, timeout):
            if "facts" not in payload["format"]["properties"]:
                return {"message": {"content": '{"summary":""}'}}
            facts = {category: [] for category in payload["format"]["properties"]["facts"]["required"]}
            facts["technology"] = [{
                "statement": "Circle Optics manufactures medical lasers for hospitals.",
                "source_url": "https://circleoptics.com/technology",
                "evidence_quote": "Circle Optics enables accurate three dimensional mapping.",
                "confidence": "high",
            }]
            return {"message": {"content": json.dumps({"summary": "", "facts": facts})}}

        result = analyze_profile(profile, transport=fake_transport)
        self.assertEqual(result["facts"]["technology"], [])

    def test_skips_model_when_no_content_is_available(self):
        profile = {
            "company": "Unavailable Co",
            "website": "",
            "extraction_status": "no_content_available",
        }
        result = analyze_profile(
            profile,
            transport=lambda *_: self.fail("transport should not be called"),
        )
        self.assertEqual(result["analysis_status"], "no_content_available")
        self.assertIsNone(result["model"])

    def test_selects_company_case_insensitively(self):
        payload = {"profiles": [{
            "company": "Circle Optics",
            "website": "",
            "extraction_status": "no_content_available",
        }]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = analyze_evidence_file(path, "circle optics")
        self.assertEqual(result["analysis"]["company"], "Circle Optics")

    def test_reports_missing_company(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            path.write_text('{"profiles": []}', encoding="utf-8")
            with self.assertRaises(AnalysisError):
                analyze_evidence_file(path, "Missing")


if __name__ == "__main__":
    unittest.main()
