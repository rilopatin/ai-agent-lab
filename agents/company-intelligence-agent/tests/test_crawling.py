from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path

from company_intel.cli import apply_url_overrides, build_parser
from company_intel.crawling import CompanyCrawl, CompanySiteCrawler, access_category
from company_intel.fetch import FetchError
from company_intel.fetch import FetchedPage
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


class RecoveringRateLimitedFetcher(FakeFetcher):
    def __init__(self, pages: dict[str, str]) -> None:
        super().__init__(pages)
        self.calls = 0

    def get_text(self, url: str) -> str:
        self.calls += 1
        if self.calls == 1:
            raise FetchError("HTTP Error 429: Too Many Requests")
        return super().get_text(url)


class BrowserFallbackFetcher(FakeFetcher):
    def get_text(self, url: str) -> str:
        raise FetchError("HTTP Error 429: Too Many Requests")

    def get_text_browser(self, url: str) -> str:
        return super().get_text(url)


class MissingLinkFetcher(FakeFetcher):
    def get_text(self, url: str) -> str:
        if url not in self.pages:
            raise FetchError("HTTP Error 404: Not Found")
        return super().get_text(url)


class ErrorLandingPageFetcher:
    def get_page(self, url: str):
        raise FetchError("HTTP Error 503: Service Unavailable")

    def get_page_browser(self, url: str, allow_http_errors: bool = False):
        return FetchedPage(
            "<html><title>Coming Soon</title><p>Check back soon!</p></html>",
            url,
            503,
        )


class RedirectingFetcher:
    def get_page(self, url: str):
        if url == "https://old.example/":
            return FetchedPage(
                "<html><a href='/about'>About</a><p>Home</p></html>",
                "https://new.example/home",
                200,
            )
        if url == "https://new.example/about":
            return FetchedPage("<html><p>About us</p></html>", url, 200)
        raise AssertionError(url)


class TestCrawler(CompanySiteCrawler):
    @staticmethod
    def _robots(start_url: str):
        return None


class UnreachableRobotsCrawler(CompanySiteCrawler):
    @staticmethod
    def _robots(company: CompanyRecord):
        from company_intel.robots_audit import RobotsInspection

        return RobotsInspection(
            company=company.name,
            site_url=company.official_url,
            robots_url=f"{company.official_url.rstrip('/')}/robots.txt",
            http_status=500,
            final_url=f"{company.official_url.rstrip('/')}/robots.txt",
            state="robots_temporarily_unreachable",
            crawl_allowed=False,
            crawl_delay=0.0,
            path_permissions={},
            rules=[],
            content="",
            error="HTTP Error 500: Internal Server Error",
        )


class CompanySiteCrawlerTests(unittest.TestCase):
    def test_crawl_defaults_to_entire_portfolio(self) -> None:
        args = build_parser().parse_args(["crawl"])
        self.assertEqual(args.limit, 0)

    def test_reports_unreachable_site_without_robots_category(self) -> None:
        company = CompanyRecord("Example", "https://portfolio.test", "https://example.com/")
        result = UnreachableRobotsCrawler(
            FakeFetcher({}), max_pages=1, max_depth=0, request_delay=0,
            rate_limit_retries=0,
        ).crawl(company)
        self.assertEqual(result.status, "site_unavailable")
        self.assertEqual(access_category(result), "site_unavailable")
        self.assertIn("robots_unreachable:", result.errors[0])

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
        result = TestCrawler(
            fetcher, max_pages=10, max_depth=1, request_delay=0,
            rate_limit_retries=0,
        ).crawl(company)
        self.assertEqual(result.status, "ok")
        self.assertEqual([page.url for page in result.pages], [
            "https://example.com/", "https://example.com/about"
        ])

    def test_skips_email_and_service_links(self) -> None:
        home = """
        <html><body><p>Home</p>
        <a href='info@example.com'>Email</a>
        <a href='/cdn-cgi/l/email-protection'>Protected email</a>
        <a href='/imunify-bot-check'>Bot check</a>
        <a href='/about'>About</a>
        </body></html>
        """
        fetcher = FakeFetcher({
            "https://example.com/": home,
            "https://example.com/about": "<html><p>About</p></html>",
        })
        company = CompanyRecord("Example", "https://portfolio.test", "https://example.com/")
        result = TestCrawler(
            fetcher, max_pages=10, max_depth=1, request_delay=0,
            rate_limit_retries=0,
        ).crawl(company)
        self.assertEqual(result.status, "ok")
        self.assertEqual([page.url for page in result.pages], [
            "https://example.com/", "https://example.com/about",
        ])

    def test_minor_link_failure_is_ok_with_warnings(self) -> None:
        fetcher = MissingLinkFetcher({
            "https://example.com/": "<html><a href='/missing'>Missing</a><p>Home</p></html>",
        })
        company = CompanyRecord("Example", "https://portfolio.test", "https://example.com/")
        result = TestCrawler(
            fetcher, max_pages=10, max_depth=1, request_delay=0,
            rate_limit_retries=0,
        ).crawl(company)
        self.assertEqual(result.status, "ok_with_warnings")

    def test_stops_company_after_rate_limit(self) -> None:
        home = "<html><a href='/about'>About</a><a href='/news'>News</a><p>Home</p></html>"
        fetcher = RateLimitedFetcher({"https://example.com/": home})
        company = CompanyRecord("Example", "https://portfolio.test", "https://example.com/")
        result = TestCrawler(
            fetcher, max_pages=10, max_depth=1, request_delay=0,
            rate_limit_retries=0,
        ).crawl(company)
        self.assertEqual(result.status, "partial")
        self.assertEqual(len(fetcher.calls), 2)
        self.assertIn("crawl_stopped: automation rate limit reached", result.errors)

    def test_retries_rate_limit_and_recovers(self) -> None:
        fetcher = RecoveringRateLimitedFetcher({
            "https://example.com/": "<html><p>Home</p></html>"
        })
        company = CompanyRecord("Example", "https://portfolio.test", "https://example.com/")
        result = TestCrawler(
            fetcher, max_pages=1, max_depth=0, request_delay=0,
            rate_limit_retries=1, rate_limit_backoff=0,
        ).crawl(company)
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.pages), 1)
        self.assertEqual(fetcher.calls, 2)

    def test_uses_browser_fallback_after_rate_limit(self) -> None:
        fetcher = BrowserFallbackFetcher({
            "https://example.com/": "<html><p>Home</p></html>"
        })
        company = CompanyRecord("Example", "https://portfolio.test", "https://example.com/")
        result = TestCrawler(
            fetcher, max_pages=1, max_depth=0, request_delay=0,
            rate_limit_retries=0, rate_limit_backoff=0,
        ).crawl(company)
        self.assertEqual(result.status, "ok_browser_fallback")
        self.assertEqual(len(result.pages), 1)
        self.assertIn("browser_fallback_succeeded: https://example.com/", result.errors)

    def test_detects_placeholder_homepage(self) -> None:
        fetcher = FakeFetcher({
            "https://example.com/": "<html><title>Coming Soon</title><p>Check back soon!</p></html>"
        })
        company = CompanyRecord("Example", "https://portfolio.test", "https://example.com/")
        result = TestCrawler(
            fetcher, max_pages=3, max_depth=1, request_delay=0,
            rate_limit_retries=0,
        ).crawl(company)
        self.assertEqual(result.status, "placeholder_no_content")

    def test_classifies_rendered_http_error_page(self) -> None:
        company = CompanyRecord("Example", "https://portfolio.test", "https://example.com/")
        result = TestCrawler(
            ErrorLandingPageFetcher(), max_pages=3, max_depth=1,
            request_delay=0, rate_limit_retries=0,
        ).crawl(company)
        self.assertEqual(result.status, "placeholder_no_content")

    def test_resolves_links_from_final_redirect_url(self) -> None:
        company = CompanyRecord("Example", "https://portfolio.test", "https://old.example/")
        result = TestCrawler(
            RedirectingFetcher(), max_pages=2, max_depth=1,
            request_delay=0, rate_limit_retries=0,
        ).crawl(company)
        self.assertEqual([page.url for page in result.pages], [
            "https://new.example/home", "https://new.example/about",
        ])

    def test_applies_verified_url_override(self) -> None:
        company = CompanyRecord("Example", "https://portfolio.test", "https://old.test/")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "overrides.json"
            path.write_text(json.dumps({"Example": "https://new.test/"}), encoding="utf-8")
            result = apply_url_overrides([company], str(path))
        self.assertEqual(result[0].official_url, "https://new.test/")

    def test_access_audit_separates_blocking_causes(self) -> None:
        self.assertEqual(access_category(CompanyCrawl(
            "A", "https://a.test", "failed", [], ["blocked_by_robots: https://a.test"]
        )), "robots_restricted")
        self.assertEqual(access_category(CompanyCrawl(
            "B", "https://b.test", "failed", [], ["fetch_failed: HTTP Error 429"]
        )), "automation_rate_limited")
        self.assertEqual(access_category(CompanyCrawl(
            "C", "https://c.test", "failed", [], ["anti_bot_challenge: cloudflare"]
        )), "anti_bot_challenge")
        self.assertEqual(access_category(CompanyCrawl(
            "D", "", "no_url_provided", [], []
        )), "no_url_provided")
        self.assertEqual(access_category(CompanyCrawl(
            "E", "https://e.test", "failed", [], ["fetch_failed: DNS error"]
        )), "site_unavailable")

    def test_detects_browser_challenge_page(self) -> None:
        fetcher = FakeFetcher({
            "https://example.com/": "<html><title>Just a moment...</title><p>Enable JavaScript and cookies to continue</p></html>"
        })
        company = CompanyRecord("Example", "https://portfolio.test", "https://example.com/")
        result = TestCrawler(
            fetcher, max_pages=3, max_depth=1, request_delay=0,
            rate_limit_retries=0,
        ).crawl(company)
        self.assertEqual(result.status, "failed")
        self.assertEqual(access_category(result), "anti_bot_challenge")


if __name__ == "__main__":
    unittest.main()
