from __future__ import annotations

import unittest
from urllib.error import HTTPError

from company_intel.models import CompanyRecord
from company_intel.robots_audit import RobotsInspector


class FakeRobotsInspector(RobotsInspector):
    def __init__(self, status=200, content="", error_code=None):
        super().__init__(timeout_seconds=0)
        self.status = status
        self.content = content
        self.error_code = error_code

    def _fetch(self, url):
        if self.error_code:
            raise HTTPError(url, self.error_code, "error", {}, None)
        return self.status, url, self.content


class RobotsInspectorTests(unittest.TestCase):
    def setUp(self):
        self.company = CompanyRecord("Example", "https://portfolio.test", "https://example.com/")

    def test_reports_full_disallow(self):
        result = FakeRobotsInspector(content="User-agent: *\nDisallow: /\n").inspect(self.company)
        self.assertEqual(result.state, "full_disallow")
        self.assertFalse(result.crawl_allowed)

    def test_reports_partial_restrictions(self):
        result = FakeRobotsInspector(
            content="User-agent: *\nDisallow: /admin\nDisallow: /careers\n"
        ).inspect(self.company)
        self.assertEqual(result.state, "partial_restrictions")
        self.assertTrue(result.path_permissions["/about"])
        self.assertFalse(result.path_permissions["/careers"])

    def test_treats_404_as_unavailable_rules_file(self):
        result = FakeRobotsInspector(error_code=404).inspect(self.company)
        self.assertEqual(result.state, "robots_unavailable_4xx")
        self.assertTrue(result.crawl_allowed)


if __name__ == "__main__":
    unittest.main()
