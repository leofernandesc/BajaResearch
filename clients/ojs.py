"""Repair stale PDF links for a publisher whose current OJS page is public.

This is a bounded publisher-page fallback, not broad web crawling. A matching
DOI and title are required before metadata or a candidate PDF is accepted.
The regular PDF verifier still decides whether the candidate is usable.
"""

from __future__ import annotations

from html.parser import HTMLParser
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urljoin, urlparse
import logging
import time

import httpx

from .http import USER_AGENT
try:
    from ..models import Paper, normalize_doi, normalize_title
except ImportError:  # pragma: no cover
    from models import Paper, normalize_doi, normalize_title


logger = logging.getLogger(__name__)
_HOST = "rspsciencehub.com"
_BASE = "https://rspsciencehub.com/index.php/journal"


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a" and values.get("href"):
            self.links.append(values["href"] or "")
        if tag == "meta" and values.get("name") and values.get("content"):
            self.meta[values["name"].casefold()] = values["content"] or ""


class OjsPublisherResolver:
    def __init__(self, verifier: object, *, timeout: float = 8.0,
                 http_client: httpx.Client | None = None) -> None:
        self.verifier = verifier
        self.timeout = max(1.0, min(float(timeout), 20.0))
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            timeout=self.timeout, follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
        )

    @staticmethod
    def _is_publisher_url(url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme == "https" and parsed.hostname == _HOST and not (
            parsed.username or parsed.password or parsed.port
        )

    @classmethod
    def is_candidate(cls, paper: Paper) -> bool:
        if paper.metadata.get("publisher_pdf_url"):
            return False
        checked_at = paper.metadata.get("ojs_resolution_checked_at")
        if checked_at:
            try:
                if datetime.fromisoformat(str(checked_at)) > datetime.now(timezone.utc) - timedelta(hours=1):
                    return False
            except (TypeError, ValueError):
                pass
        return bool(paper.doi) and any(
            cls._is_publisher_url(url) for url in paper.candidate_full_text_urls()
        )

    def _page(self, url: str) -> _Page | None:
        if not self._is_publisher_url(url):
            return None
        target_error = getattr(self.verifier, "_target_error", None)
        if callable(target_error) and target_error(url) is not None:
            return None
        try:
            with self._client.stream("GET", url, follow_redirects=False,
                                     timeout=self.timeout) as response:
                if response.status_code != 200 or "html" not in response.headers.get("content-type", ""):
                    return None
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > 512 * 1024:
                        return None
                page = _Page()
                page.feed(bytes(content).decode("utf-8", errors="replace"))
                return page
        except (httpx.RequestError, UnicodeError, ValueError):
            return None

    def resolve(self, paper: Paper, *, deadline: float | None = None) -> list[str]:
        if not self.is_candidate(paper):
            return []
        paper.metadata["ojs_resolution_checked_at"] = datetime.now(timezone.utc).isoformat()
        if deadline is not None and time.monotonic() >= deadline:
            return []
        search_url = f"{_BASE}/search/search?query={quote(paper.title, safe='')}"
        results = self._page(search_url)
        if results is None:
            return []
        article_urls = list(dict.fromkeys(
            urljoin(_BASE + "/", href) for href in results.links
            if "/article/view/" in href
        ))[:3]
        for article_url in article_urls:
            if deadline is not None and time.monotonic() >= deadline:
                break
            page = self._page(article_url)
            if page is None:
                continue
            if (normalize_doi(page.meta.get("citation_doi")) != paper.doi
                    or normalize_title(page.meta.get("citation_title", "")) != normalize_title(paper.title)):
                continue
            pdf_url = page.meta.get("citation_pdf_url", "")
            if not self._is_publisher_url(pdf_url):
                continue
            abstract = page.meta.get("citation_abstract") or page.meta.get("dc.description")
            if abstract:
                paper.abstract = abstract
                paper.metadata["publisher_abstract"] = abstract
            existing = list(paper.metadata.get("full_text_candidates") or [])
            paper.metadata["full_text_candidates"] = list(dict.fromkeys([pdf_url, *existing]))
            paper.open_access_url = pdf_url
            paper.metadata["publisher_pdf_url"] = pdf_url
            paper.landing_url = article_url
            paper.provenance.setdefault("publisher", {})["metadata_url"] = article_url
            logger.info("event=ojs_publisher_resolution status=matched paper_id=%s", paper.internal_id)
            return [pdf_url]
        logger.info("event=ojs_publisher_resolution status=not_found paper_id=%s", paper.internal_id)
        return []

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


__all__ = ["OjsPublisherResolver"]
