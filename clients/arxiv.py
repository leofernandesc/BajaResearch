"""Rate-limited arXiv Atom API supplement for free preprints (no scraping)."""

from __future__ import annotations

import re
import threading
import time
from typing import Any
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import httpx

from .base import SourceError
from .http import USER_AGENT
try:
    from ..models import Paper, normalize_doi
except ImportError:  # pragma: no cover
    from models import Paper, normalize_doi


BASE_URL = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
_RATE_LOCK = threading.Lock()
_LAST_REQUEST = 0.0


def _content(element: ET.Element, name: str) -> str | None:
    value = element.findtext(name)
    return " ".join(value.split()) if value and value.strip() else None


def _entry_to_paper(entry: ET.Element) -> Paper | None:
    title = _content(entry, f"{ATOM}title")
    raw_id = _content(entry, f"{ATOM}id")
    if not title or not raw_id:
        return None
    paper_id = urlparse(raw_id).path.rsplit("/", 1)[-1]
    pdf_url = next((
        link.get("href") for link in entry.findall(f"{ATOM}link")
        if link.get("type") == "application/pdf" or link.get("title") == "pdf"
    ), None)
    if pdf_url and pdf_url.startswith("http://arxiv.org/"):
        pdf_url = "https://" + pdf_url[len("http://"):]
    published = _content(entry, f"{ATOM}published")
    year = int(published[:4]) if published and published[:4].isdigit() else None
    authors = [
        name for author in entry.findall(f"{ATOM}author")
        if (name := _content(author, f"{ATOM}name"))
    ]
    topics = [category.get("term") for category in entry.findall(f"{ATOM}category") if category.get("term")]
    return Paper(
        internal_id="", title=title, authors=authors, year=year,
        abstract=_content(entry, f"{ATOM}summary"),
        document_type="preprint", doi=normalize_doi(_content(entry, f"{ARXIV}doi")),
        url=raw_id.replace("http://arxiv.org/", "https://arxiv.org/"),
        open_access_url=pdf_url,
        topics=topics, sources=["arxiv"], source_scores={"arxiv": 0.4},
        metadata={"arxiv_id": paper_id, "full_text_candidates": [pdf_url] if pdf_url else []},
    )


class ArxivClient:
    source = "arxiv"

    def __init__(self, *, timeout: float = 8.0, max_retries: int = 0,
                 http_client: httpx.Client | None = None) -> None:
        self.timeout = max(1.0, timeout)
        self._owns_client = http_client is None
        self.http = http_client or httpx.Client(
            timeout=self.timeout, follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/atom+xml"},
        )

    @property
    def configured(self) -> bool:
        return True

    def _query(self, params: dict[str, Any]) -> list[Paper]:
        global _LAST_REQUEST
        with _RATE_LOCK:
            remaining = 3.0 - (time.monotonic() - _LAST_REQUEST)
            if remaining > 0:
                time.sleep(remaining)
            _LAST_REQUEST = time.monotonic()
            try:
                response = self.http.get(BASE_URL, params=params, timeout=self.timeout)
            except httpx.RequestError as exc:
                raise SourceError(self.source, "arXiv request failed", code="network_error", retryable=True) from exc
        if response.status_code == 429:
            raise SourceError(self.source, "arXiv rate limited", status=429,
                              code="rate_limited", retryable=True)
        if response.status_code >= 500:
            raise SourceError(self.source, "arXiv server unavailable", status=response.status_code,
                              code="upstream_5xx", retryable=True)
        if response.status_code >= 400:
            raise SourceError(self.source, "arXiv rejected request", status=response.status_code,
                              code="http_error")
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise SourceError(self.source, "invalid arXiv Atom feed", code="invalid_payload") from exc
        return [paper for entry in root.findall(f"{ATOM}entry")
                if (paper := _entry_to_paper(entry))]

    def search(self, query: str, *, limit: int = 10,
               year_from: int | None = None, year_to: int | None = None,
               open_access_only: bool = True) -> list[Paper]:
        terms = [word for word in re.findall(r"[A-Za-z0-9]+", query) if word]
        topic = terms[-1] if terms else "Baja"
        context = 'all:"Formula SAE" OR all:"Baja SAE" OR all:"Formula Student"'
        papers = self._query({
            "search_query": f"all:{topic} AND ({context})",
            "start": 0, "max_results": max(1, min(limit, 20)),
        })
        return [paper for paper in papers if (year_from is None or paper.year is None or paper.year >= year_from)
                and (year_to is None or paper.year is None or paper.year <= year_to)
                and (not open_access_only or paper.open_access_url)]

    def get(self, identifier: str) -> Paper | None:
        raw = identifier.strip().removeprefix("arxiv:")
        if not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z.-]+/\d{7})(?:v\d+)?", raw, re.I):
            return None
        papers = self._query({"id_list": raw, "max_results": 1})
        return papers[0] if papers else None

    def close(self) -> None:
        if self._owns_client:
            self.http.close()


__all__ = ["ArxivClient"]
