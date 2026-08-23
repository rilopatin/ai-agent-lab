from __future__ import annotations

import unittest

from company_intel.crawling import CompanySiteCrawler
from company_intel.fetch import FetchError
from company_intel.models import CompanyRecord


class FakeFetcher:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages

    def get_text(self, url: str) -> str:
        return self.pages[url]


class RateLimitedFetcher(FakeFetcher):
    def __init__(self, pages: dict[str, str]) -> None:
        super().__init__(pages)
        self.calls: list[str] = []

    def get_text(self, url: str) -> str:
        self.calls.append(url)
        if len(self.calls) > 1:
            raise FetchError("HTTP Error 429: Too Many Requests")
        return super().get_text(url)


class TestCrawler(CompanySiteCrawler):
    @staticmethod
    def _robots(start_url: str):
        return None


class CompanySiteCrawlerTests(unittest.TestCase):
    def test_stays_on_domain_prioritizes_relevant_pages_and_deduplicates(self) -> None:
        home = """
        <html><head><title>Example</title></head><body>
        <a href='/misc'>Misc</a><a href='/about'>About</a>
        <a href='https://outside.example/news'>Outside</a>
        <a href='/brochure.pdf'>PDF</a><p>Home text</p>
        </body></html>
        """
        about = "<html><title>About</title><p>Team and founders.</p></html>"
        misc_duplicate = "<html><title>About</title><p>Team and founders.</p></html>"
        fetcher = FakeFetcher({
            "https://example.com/": home,
            "https://example.com/about": about,
            "https://example.com/misc": misc_duplicate,
        })
        company = CompanyRecord("Example", "https://portfolio.test", "https://example.com/")
        result = TestCrawler(fetcher, max_pages=10, max_depth=1, request_delay=0).crawl(company)
        self.assertEqual(result.status, "ok")
        self.assertEqual([page.url for page in result.pages], [
            "https://example.com/", "https://example.com/about"
        ])

    def test_stops_company_after_rate_limit(self) -> None:
        home = "<html><a href='/about'>About</a><a href='/news'>News</a><p>Home</p></html>"
        fetcher = RateLimitedFetcher({"https://example.com/": home})
        company = CompanyRecord("Example", "https://portfolio.test", "https://example.com/")
        result = TestCrawler(fetcher, max_pages=10, max_depth=1, request_delay=0).crawl(company)
        self.assertEqual(result.status, "partial")
        self.assertEqual(len(fetcher.calls), 2)
        self.assertIn("crawl_stopped: site rate limit reached", result.errors)


if __name__ == "__main__":
    unittest.main()
