"""OpenAlex works client (the primary BAJA Research source)."""

from __future__ import annotations

import os
from typing import Any, Mapping
from urllib.parse import quote

from .base import (
    SourceError,
    abstract_from_inverted_index,
    as_list,
    as_mapping,
    first_text,
    source_id,
    text,
    year_from_payload,
)
from .http import JsonHttpClient
try:
    from ..models import Paper, normalize_doi
except ImportError:  # pragma: no cover - direct test imports
    from models import Paper, normalize_doi


BASE_URL = "https://api.openalex.org"


def _normalized_source_score(value: Any) -> float:
    try:
        raw = max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0
    # OpenAlex relevance is not guaranteed to be in [0, 1]. Compress it so
    # it remains one ranking signal rather than dominating other signals.
    return raw if raw <= 1.0 else raw / (1.0 + raw)


def _topic_names(work: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for topic in as_list(work.get("topics")):
        if isinstance(topic, Mapping):
            candidate = text(topic.get("display_name") or topic.get("name"))
            if candidate:
                values.append(candidate)
    for concept in as_list(work.get("concepts")):
        if isinstance(concept, Mapping):
            candidate = text(concept.get("display_name") or concept.get("name"))
            if candidate:
                values.append(candidate)
    for keyword in as_list(work.get("keywords")):
        if isinstance(keyword, Mapping):
            candidate = text(keyword.get("display_name") or keyword.get("keyword"))
        else:
            candidate = text(keyword)
        if candidate:
            values.append(candidate)
    return values


def _work_to_paper(work: Mapping[str, Any]) -> Paper | None:
    title = text(work.get("title") or work.get("display_name"))
    if not title:
        return None
    primary_location = as_mapping(work.get("primary_location"))
    source = as_mapping(primary_location.get("source"))
    best_oa = as_mapping(work.get("best_oa_location"))
    open_access = as_mapping(work.get("open_access"))
    host_venue = work.get("host_venue")
    if isinstance(host_venue, Mapping):
        host_venue = host_venue.get("display_name") or host_venue.get("name")
    is_oa = bool(open_access.get("is_oa")) or bool(best_oa.get("is_oa"))
    oa_url = None
    if is_oa:
        oa_url = text(
            best_oa.get("pdf_url")
            or best_oa.get("landing_page_url")
            or open_access.get("oa_url")
        )
    authors: list[str] = []
    for authorship in as_list(work.get("authorships")):
        if isinstance(authorship, Mapping):
            author = as_mapping(authorship.get("author"))
            name = text(author.get("display_name") or authorship.get("raw_author_name"))
            if name:
                authors.append(name)

    raw_doi = work.get("doi") or as_mapping(work.get("ids")).get("doi")
    doi = normalize_doi(raw_doi)
    oa_id = source_id(work.get("id"), prefix="https://openalex.org/")
    if oa_id and oa_id.lower().startswith("openalex:"):
        oa_id = oa_id.split(":", 1)[1]
    url = text(
        primary_location.get("landing_page_url")
        or work.get("landing_page_url")
        or work.get("id")
    )
    raw_relevance = work.get("relevance_score")
    metadata: dict[str, Any] = {}
    if raw_relevance is not None:
        try:
            metadata["openalex_relevance_score"] = float(raw_relevance)
        except (TypeError, ValueError):
            pass
    if is_oa:
        metadata["open_access_confirmed"] = True
    return Paper(
        internal_id="",
        title=title,
        authors=authors,
        year=year_from_payload(work.get("publication_year")),
        abstract=abstract_from_inverted_index(work.get("abstract_inverted_index")),
        venue=text(source.get("display_name") or host_venue),
        doi=doi,
        openalex_id=oa_id,
        citation_count=work.get("cited_by_count"),
        url=url,
        open_access_url=oa_url,
        topics=_topic_names(work),
        sources=["openalex"],
        source_scores={"openalex": _normalized_source_score(raw_relevance)},
        metadata=metadata,
    )


class OpenAlexClient:
    source = "openalex"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: float = 15.0,
        max_retries: int = 2,
        http: JsonHttpClient | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENALEX_API_KEY") or None
        self.http = http or JsonHttpClient(
            BASE_URL,
            self.source,
            timeout=timeout,
            max_retries=max_retries,
        )

    @property
    def configured(self) -> bool:
        # OpenAlex permits keyless use; this reports credential configuration,
        # while the service separately reports that the source is key-optional.
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
            "search": query,
            "per-page": max(1, min(int(limit), 100)),
            "page": 1,
        }
        filters: list[str] = []
        if year_from is not None:
            filters.append(f"from_publication_year:{int(year_from)}")
        if year_to is not None:
            filters.append(f"to_publication_year:{int(year_to)}")
        if open_access_only:
            filters.append("open_access.is_oa:true")
        if filters:
            params["filter"] = ",".join(filters)
        if self.api_key:
            params["api_key"] = self.api_key
        payload = self.http.get_json("/works", params=params)
        results = as_mapping(payload).get("results")
        if not isinstance(results, list):
            raise SourceError(
                self.source,
                "OpenAlex returned no usable results array",
                code="invalid_payload",
            )
        papers = [_work_to_paper(as_mapping(item)) for item in results]
        return [paper for paper in papers if paper is not None]

    def get(self, identifier: str) -> Paper | None:
        doi = normalize_doi(identifier)
        if doi:
            target = quote(f"https://doi.org/{doi}", safe="")
        else:
            target = quote(identifier.strip().rstrip("/").rsplit("/", 1)[-1], safe="")
        params = {"api_key": self.api_key} if self.api_key else None
        payload = self.http.get_json(f"/works/{target}", params=params)
        if not isinstance(payload, Mapping):
            return None
        return _work_to_paper(payload)

    def related(self, identifier: str, *, limit: int = 5) -> list[Paper]:
        doi = normalize_doi(identifier)
        target = quote(
            f"https://doi.org/{doi}" if doi else identifier.strip().rstrip("/").rsplit("/", 1)[-1],
            safe="",
        )
        params = {"api_key": self.api_key} if self.api_key else None
        payload = self.http.get_json(f"/works/{target}", params=params)
        related_ids = as_mapping(payload).get("related_works")
        if not isinstance(related_ids, list):
            return []
        papers: list[Paper] = []
        for related_id in related_ids[: max(1, min(int(limit), 10))]:
            try:
                related_payload = self.http.get_json(
                    f"/works/{quote(str(related_id).rstrip('/').rsplit('/', 1)[-1], safe='')}",
                    params=params,
                )
            except SourceError:
                continue
            paper = _work_to_paper(as_mapping(related_payload))
            if paper:
                papers.append(paper)
        return papers

    def close(self) -> None:
        self.http.close()


__all__ = ["OpenAlexClient"]
