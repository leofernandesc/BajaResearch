"""Semantic Scholar Academic Graph and Recommendations clients."""

from __future__ import annotations

import os
from typing import Any, Mapping
from urllib.parse import quote

from .base import SourceError, as_list, as_mapping, text, year_from_payload
from .http import JsonHttpClient
try:
    from ..models import Paper, normalize_doi
except ImportError:  # pragma: no cover - direct test imports
    from models import Paper, normalize_doi


GRAPH_BASE_URL = "https://api.semanticscholar.org/graph/v1"
RECOMMENDATIONS_BASE_URL = "https://api.semanticscholar.org/recommendations/v1"
PAPER_FIELDS = (
    "paperId,title,abstract,authors,year,venue,externalIds,url,openAccessPdf,"
    "citationCount,influentialCitationCount,fieldsOfStudy,s2FieldsOfStudy,tldr"
)


def _paper_to_model(item: Mapping[str, Any]) -> Paper | None:
    title = text(item.get("title"))
    if not title:
        return None
    authors: list[str] = []
    for author in as_list(item.get("authors")):
        if isinstance(author, Mapping):
            name = text(author.get("name"))
            if name:
                authors.append(name)
    external_ids = as_mapping(item.get("externalIds"))
    doi = normalize_doi(external_ids.get("DOI") or item.get("doi"))
    paper_id = text(item.get("paperId"))
    oa_pdf = as_mapping(item.get("openAccessPdf"))
    oa_url = text(oa_pdf.get("url"))
    topics: list[str] = []
    for field in ("fieldsOfStudy",):
        topics.extend(str(value) for value in as_list(item.get(field)) if text(value))
    for field in as_list(item.get("s2FieldsOfStudy")):
        if isinstance(field, Mapping):
            value = text(field.get("category"))
            if value:
                topics.append(value)
    metadata: dict[str, Any] = {}
    tldr = as_mapping(item.get("tldr"))
    if text(tldr.get("text")):
        metadata["tldr"] = text(tldr.get("text"))
    if item.get("influentialCitationCount") is not None:
        metadata["influential_citation_count"] = item.get("influentialCitationCount")
    if oa_url:
        metadata["open_access_confirmed"] = True
    return Paper(
        internal_id="",
        title=title,
        authors=authors,
        year=year_from_payload(item.get("year")),
        abstract=text(item.get("abstract")),
        venue=text(item.get("venue")),
        doi=doi,
        semantic_scholar_id=paper_id,
        citation_count=item.get("citationCount"),
        url=text(item.get("url"))
        or (f"https://www.semanticscholar.org/paper/{paper_id}" if paper_id else None),
        open_access_url=oa_url,
        topics=topics,
        sources=["semantic_scholar"],
        # The search endpoint supplies relevance ordering, not a stable score.
        source_scores={"semantic_scholar": 0.5},
        metadata=metadata,
    )


class SemanticScholarClient:
    source = "semantic_scholar"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: float = 8.0,
        max_retries: int = 1,
        http: JsonHttpClient | None = None,
        recommendations_http: JsonHttpClient | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("SEMANTIC_SCHOLAR_API_KEY") or None
        headers = {"x-api-key": self.api_key} if self.api_key else {}
        self.http = http or JsonHttpClient(
            GRAPH_BASE_URL,
            self.source,
            timeout=timeout,
            max_retries=max_retries,
            headers=headers,
        )
        self.recommendations_http = recommendations_http or JsonHttpClient(
            RECOMMENDATIONS_BASE_URL,
            self.source,
            timeout=timeout,
            max_retries=max_retries,
            headers=headers,
        )
        if self.api_key:
            self.http.set_header("x-api-key", self.api_key)
            self.recommendations_http.set_header("x-api-key", self.api_key)

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        year_from: int | None = None,
        year_to: int | None = None,
        open_access_only: bool = False,
    ) -> list[Paper]:
        params: dict[str, Any] = {
            "query": query,
            "limit": max(1, min(int(limit), 100)),
            "offset": 0,
            "fields": PAPER_FIELDS,
        }
        if year_from is not None or year_to is not None:
            params["year"] = f"{year_from or ''}-{year_to or ''}"
        if open_access_only:
            params["openAccessPdf"] = "true"
        payload = self.http.get_json("/paper/search", params=params)
        data = as_mapping(payload).get("data")
        if not isinstance(data, list):
            raise SourceError(
                self.source,
                "Semantic Scholar returned no usable data array",
                code="invalid_payload",
            )
        papers = [_paper_to_model(as_mapping(item)) for item in data]
        return [paper for paper in papers if paper is not None]

    def get(self, identifier: str) -> Paper | None:
        doi = normalize_doi(identifier)
        paper_key = f"DOI:{doi}" if doi else identifier.strip()
        fields = PAPER_FIELDS
        payload = self.http.get_json(
            f"/paper/{quote(paper_key, safe='')}",
            params={"fields": fields},
        )
        if not isinstance(payload, Mapping):
            return None
        return _paper_to_model(payload)

    def related(self, identifier: str, *, limit: int = 5) -> list[Paper]:
        doi = normalize_doi(identifier)
        paper_key = f"DOI:{doi}" if doi else identifier.strip()
        payload = self.recommendations_http.get_json(
            f"/papers/forpaper/{quote(paper_key, safe='')}",
            params={
                "limit": max(1, min(int(limit), 100)),
                "fields": PAPER_FIELDS,
            },
        )
        data = as_mapping(payload).get("recommendedPapers")
        if not isinstance(data, list):
            data = as_mapping(payload).get("data")
        papers = [_paper_to_model(as_mapping(item)) for item in as_list(data)]
        return [paper for paper in papers if paper is not None]

    def close(self) -> None:
        self.http.close()
        if self.recommendations_http is not self.http:
            self.recommendations_http.close()


__all__ = ["SemanticScholarClient"]
