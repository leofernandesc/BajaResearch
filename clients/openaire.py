"""OpenAIRE Graph v3 discovery of repository-hosted academic works."""

from __future__ import annotations

from html import unescape
import re
from typing import Any, Mapping

from .base import SourceError, as_list, as_mapping, text, year_from_payload
from .http import JsonHttpClient
try:
    from ..models import Paper, normalize_document_type, normalize_doi
except ImportError:  # pragma: no cover - direct module imports
    from models import Paper, normalize_document_type, normalize_doi


BASE_URL = "https://api.openaire.eu/graph/v3"
_PDF_PATHS = (".pdf", "/bitstream/", "/bitstreams/", "/datastream/pdf", "/download", "/content")


def _plain_text(value: Any) -> str | None:
    raw = text(value)
    if not raw:
        return None
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", raw)).split()) or None


def _direct_pdf_candidate(value: Any) -> str | None:
    url = text(value)
    if not url or not url.lower().startswith("https://"):
        return None
    path = url.split("?", 1)[0].casefold()
    return url if any(marker in path for marker in _PDF_PATHS) else None


def _record_to_paper(record: Mapping[str, Any], rank: int = 0) -> Paper | None:
    title = _plain_text(record.get("mainTitle"))
    if not title:
        return None
    instances = [as_mapping(item) for item in as_list(record.get("instances"))]
    pids = [as_mapping(item) for item in as_list(record.get("pids"))]
    for instance in instances:
        pids.extend(as_mapping(item) for item in as_list(instance.get("pids")))
    doi = next((
        normalize_doi(pid.get("value"))
        for pid in pids if str(pid.get("scheme") or "").casefold() == "doi" and normalize_doi(pid.get("value"))
    ), None)
    types = [normalize_document_type(item.get("type")) for item in instances]
    document_type = next((kind for kind in types if kind == "bachelor_thesis"), None)
    if document_type is None:
        document_type = next((kind for kind in types if kind in {"master_thesis", "doctoral_thesis", "thesis", "monograph"}), None)
    if document_type is None:
        document_type = next((kind for kind in types if kind), None)
    urls = list(dict.fromkeys(
        url for instance in instances for url in as_list(instance.get("urls"))
        if isinstance(url, str) and url.startswith("https://")
    ))
    candidates = [candidate for url in urls if (candidate := _direct_pdf_candidate(url))]
    authors = [
        name for item in as_list(record.get("authors"))
        if (name := _plain_text(as_mapping(item).get("fullName"))) and not name.casefold().startswith("null ")
    ]
    descriptions = [
        _plain_text(item if isinstance(item, str) else as_mapping(item).get("description"))
        for item in as_list(record.get("descriptions"))
    ]
    topics = [
        name for item in as_list(record.get("subjects"))
        if (name := _plain_text(as_mapping(as_mapping(item).get("subject")).get("value")))
    ]
    citation = as_mapping(as_mapping(record.get("indicators")).get("citationImpact")).get("citationCount")
    score = max(0.1, 1.0 / (1.0 + max(0, rank)))
    return Paper(
        internal_id="",
        title=title,
        authors=authors,
        year=year_from_payload(str(record.get("publicationDate") or "")[:4]),
        abstract=next((item for item in descriptions if item), None),
        venue=text(as_mapping(record.get("container")).get("name")) or text(record.get("publisher")),
        document_type=document_type,
        doi=doi,
        citation_count=citation,
        url=next((url for url in urls if not _direct_pdf_candidate(url)), None),
        open_access_url=candidates[0] if candidates else None,
        topics=topics,
        sources=["openaire"],
        source_scores={"openaire": score},
        metadata={"full_text_candidates": candidates, "openaire_id": text(record.get("id"))},
    )


class OpenAIREClient:
    source = "openaire"

    def __init__(self, *, timeout: float = 8.0, max_retries: int = 1, http: JsonHttpClient | None = None) -> None:
        self.http = http or JsonHttpClient(BASE_URL, self.source, timeout=timeout, max_retries=max_retries)

    @property
    def configured(self) -> bool:
        return False  # Public endpoint; no credential is needed.

    def search(
        self, query: str, *, limit: int = 10, year_from: int | None = None,
        year_to: int | None = None, open_access_only: bool = False,
    ) -> list[Paper]:
        params: dict[str, Any] = {
            "search": query,
            "type": "publication",
            "pageSize": max(1, min(int(limit), 100)),
        }
        if year_from is not None:
            params["fromPublicationYear"] = int(year_from)
        if year_to is not None:
            params["toPublicationYear"] = int(year_to)
        payload = self.http.get_json("/research-products", params=params)
        results = as_mapping(payload).get("results")
        if not isinstance(results, list):
            raise SourceError(self.source, "OpenAIRE returned no usable results array", code="invalid_payload")
        papers = [_record_to_paper(as_mapping(item), rank) for rank, item in enumerate(results)]
        return [paper for paper in papers if paper is not None]

    def get(self, identifier: str) -> Paper | None:
        doi = normalize_doi(identifier)
        if not doi:
            return None
        payload = self.http.get_json("/research-products", params={"search": doi, "type": "publication", "pageSize": 5})
        for rank, item in enumerate(as_list(as_mapping(payload).get("results"))):
            paper = _record_to_paper(as_mapping(item), rank)
            if paper and paper.doi == doi:
                return paper
        return None

    def close(self) -> None:
        self.http.close()


__all__ = ["OpenAIREClient"]
