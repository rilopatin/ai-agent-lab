import json
import tempfile
import unittest
from pathlib import Path

from company_intel.analysis import (
    CATEGORIES, AnalysisError, analyze_all_evidence, analyze_evidence_file,
    analyze_profile, _fact_is_sane,
)


class LocalAnalysisTests(unittest.TestCase):
    def test_retries_one_temporary_local_model_failure(self):
        profile = {
            "company": "Retry Co",
            "website": "https://retry.example",
            "extraction_status": "evidence_ready",
            "contacts": {"emails": []},
            "evidence": {"products": [{
                "source_url": "https://retry.example/product",
                "source_title": "Product | Retry Co",
                "snippet": "Retry Co makes an inspection drone.",
            }]},
        }
        attempts = 0

        def flaky_transport(endpoint, payload, timeout):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise AnalysisError("temporary timeout")
            if "facts" not in payload["format"]["properties"]:
                return {"message": {"content": '{"summary":"Retry Co makes a drone."}'}}
            facts = {category: [] for category in payload["format"]["properties"]["facts"]["required"]}
            facts["products"] = [{
                "statement": "Retry Co makes an inspection drone.",
                "source_url": "https://retry.example/product",
                "evidence_quote": "Retry Co makes an inspection drone.",
                "confidence": "high",
            }]
            return {"message": {"content": json.dumps({"summary": "", "facts": facts})}}

        result = analyze_profile(profile, transport=flaky_transport, retries=1)
        self.assertEqual(result["analysis_status"], "analyzed")
        self.assertGreaterEqual(attempts, 3)

    def test_batch_saves_progress_and_resumes_without_reanalyzing(self):
        evidence = {"profiles": [
            {
                "company": "No Content Co",
                "website": "",
                "extraction_status": "no_content_available",
            },
            {
                "company": "Ready Co",
                "website": "https://ready.example",
                "extraction_status": "evidence_ready",
                "contacts": {"emails": []},
                "evidence": {"products": [{
                    "source_url": "https://ready.example/product",
                    "source_title": "Product | Ready Co",
                    "snippet": "Ready Co makes a compact inspection drone.",
                }]},
            },
        ]}
        calls = []

        def fake_transport(endpoint, payload, timeout):
            calls.append(payload)
            if "facts" not in payload["format"]["properties"]:
                return {"message": {"content": '{"summary":"Ready Co makes a drone."}'}}
            facts = {category: [] for category in payload["format"]["properties"]["facts"]["required"]}
            facts["products"] = [{
                "statement": "Ready Co makes a compact inspection drone.",
                "source_url": "https://ready.example/product",
                "evidence_quote": "Ready Co makes a compact inspection drone.",
                "confidence": "high",
            }]
            return {"message": {"content": json.dumps({"summary": "", "facts": facts})}}

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "evidence.json"
            checkpoint = Path(directory) / "checkpoint.json"
            source.write_text(json.dumps(evidence), encoding="utf-8")
            first = analyze_all_evidence(
                source, checkpoint, transport=fake_transport
            )
            first_call_count = len(calls)
            second = analyze_all_evidence(
                source, checkpoint, transport=lambda *_: self.fail("should resume")
            )
        self.assertEqual(first["completed"], 2)
        self.assertEqual(first["analyzed"], 1)
        self.assertEqual(first["no_content_available"], 1)
        self.assertGreater(first_call_count, 0)
        self.assertEqual(second["completed"], 2)

    def test_splits_a_timed_out_group_into_smaller_requests(self):
        profile = {
            "company": "Slow Co",
            "website": "https://slow.example",
            "extraction_status": "evidence_ready",
            "evidence": {
                "products": [{
                    "source_url": "https://slow.example/product",
                    "source_title": "Slow Co Product",
                    "snippet": "Slow Co makes an inspection drone.",
                }],
                "technology": [{
                    "source_url": "https://slow.example/technology",
                    "source_title": "Slow Co Technology",
                    "snippet": "Slow Co uses autonomous navigation technology.",
                }],
            },
        }

        def transport(endpoint, payload, timeout):
            if "facts" not in payload["format"]["properties"]:
                return {"message": {"content": '{"summary":"Slow Co makes a drone."}'}}
            prompt = payload["messages"][1]["content"]
            compact = json.loads(prompt[prompt.index("{"):])
            populated = [
                category for category, items in compact["evidence"].items() if items
            ]
            if len(populated) > 1:
                raise AnalysisError("local Ollama request exceeded the timeout")
            facts = {category: [] for category in CATEGORIES}
            category = populated[0]
            item = compact["evidence"][category][0]
            facts[category] = [{
                "statement": item["snippet"],
                "source_url": item["source_url"],
                "evidence_quote": item["snippet"],
                "confidence": "high",
            }]
            return {"message": {"content": json.dumps({"summary": "", "facts": facts})}}

        result = analyze_profile(profile, transport=transport, retries=0)
        self.assertEqual(result["analysis_status"], "analyzed")
        self.assertEqual(len(result["facts"]["products"]), 1)
        self.assertEqual(len(result["facts"]["technology"]), 1)

    def test_batch_refreshes_only_requested_completed_company(self):
        evidence = {"profiles": [{
            "company": "Refresh Co",
            "website": "https://refresh.example",
            "extraction_status": "no_content_available",
            "evidence": {},
        }, {
            "company": "Keep Co",
            "website": "https://keep.example",
            "extraction_status": "no_content_available",
            "evidence": {},
        }]}
        progress = []
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "evidence.json"
            checkpoint = Path(directory) / "checkpoint.json"
            source.write_text(json.dumps(evidence), encoding="utf-8")
            analyze_all_evidence(source, checkpoint)
            analyze_all_evidence(
                source,
                checkpoint,
                refresh_companies=["refresh co"],
                progress=lambda i, n, company, status: progress.append((company, status)),
            )
        self.assertIn(("Refresh Co", "no_content_available"), progress)
        self.assertIn(("Keep Co", "already_completed"), progress)

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
        self.assertNotIn("Grace Annese", combined)
        self.assertNotIn("Zak and Ian", combined)
        self.assertIn("NASA and NSF", combined)

    def test_rejects_malformed_funding_and_truncated_entity_names(self):
        self.assertFalse(_fact_is_sane(
            "funding",
            "blueflite received a 0,000 grant.",
            "blueflite received a 0,000 grant from Michigan.",
        ))
        self.assertFalse(_fact_is_sane(
            "applications",
            "Aviant secured a contract with St. based on new regulations.",
            "Aviant secured a contract with St.",
        ))
        self.assertFalse(_fact_is_sane(
            "funding",
            "Crover was recognized as a competition winner.",
            "Crover was recognized as a competition winner.",
        ))
        self.assertTrue(_fact_is_sane(
            "funding",
            "Vermeer received an SBIR Phase II award.",
            "Vermeer received an SBIR Phase II award.",
        ))

    def test_reports_no_verified_facts_when_every_model_fact_is_rejected(self):
        profile = {
            "company": "Empty Result Co",
            "website": "https://empty.example",
            "extraction_status": "evidence_ready",
            "evidence": {"funding": [{
                "source_url": "https://empty.example/news",
                "source_title": "Empty Result Co News",
                "snippet": "Empty Result Co received a 0,000 grant.",
            }]},
        }

        def transport(endpoint, payload, timeout):
            facts = {category: [] for category in CATEGORIES}
            facts["funding"] = [{
                "statement": "Empty Result Co received a 0,000 grant.",
                "source_url": "https://empty.example/news",
                "evidence_quote": "Empty Result Co received a 0,000 grant.",
                "confidence": "high",
            }]
            return {"message": {"content": json.dumps({"summary": "", "facts": facts})}}

        result = analyze_profile(profile, transport=transport)
        self.assertEqual(result["analysis_status"], "no_verified_facts")

    def test_rejects_marketing_as_news_values_as_technology_and_truncation(self):
        self.assertFalse(_fact_is_sane(
            "news",
            "Trust is the cornerstone of our military partnerships, securing a tactical advantage.",
            "Trust is the cornerstone of our military partnerships, securing a tactical advantage.",
        ))
        self.assertTrue(_fact_is_sane(
            "news",
            "Flox won the GENIUS NY grand prize.",
            "Flox won the GENIUS NY grand prize.",
        ))
        self.assertFalse(_fact_is_sane(
            "technology",
            "Geopipe emphasizes intellectual integrity, diversity and collaboration.",
            "Geopipe emphasizes intellectual integrity, diversity and collaboration.",
        ))
        self.assertFalse(_fact_is_sane(
            "news",
            "WindShape joined a visionary initiative set to a.",
            "WindShape joined a visionary initiative set to a.",
        ))

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
