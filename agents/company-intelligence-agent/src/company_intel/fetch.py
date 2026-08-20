from __future__ import annotations

import gzip
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class FetchError(RuntimeError):
    pass


@dataclass(slots=True)
class PageFetcher:
    timeout_seconds: float = 30.0
    user_agent: str = "HyperVisionCompanyMonitor/0.1 (+https://www.hypervision.ai/)"

    def get_text(self, url: str) -> str:
        request = Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Encoding": "gzip",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    payload = gzip.decompress(payload)
                charset = response.headers.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise FetchError(f"Unable to fetch {url}: {exc}") from exc

