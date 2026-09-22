"""Search the public UFSCar DSpace Discovery API for original TCC records."""

from __future__ import annotations

from typing import Any, Mapping

from .base import SourceError, as_list, as_mapping, text, year_from_payload
from .http import JsonHttpClient
try:
    from ..models import Paper, normalize_doi
except ImportError:  # pragma: no cover
    from models import Paper, normalize_doi


BASE_URL = "https://repositorio.ufscar.br/server/api"
LANDING_URL = "https://repositorio.ufscar.br/items"


def _values(metadata: Mapping[str, Any], field: str) -> list[str]:
    return [
        value for item in as_list(metadata.get(field))
        if (value := text(as_mapping(item).get("value")))
    ]


def _first(metadata: Mapping[str, Any], field: str) -> str | None:
    return next(iter(_values(metadata, field)), None)


def _record_to_paper(item: Mapping[str, Any], rank: int = 0) -> Paper | None:
    identifier = text(item.get("id") or item.get("uuid"))
    metadata = as_mapping(item.get("metadata"))
    title = _first(metadata, "dc.title") or text(item.get("name"))
    if not identifier or not title:
        return None
    year_text = _first(metadata, "dc.date.issued")
    year = year_from_payload(year_text[:4] if year_text else None)
    abstract = _first(metadata, "dc.description.resumo") or _first(metadata, "dc.description.abstract")
    doi = normalize_doi(_first(metadata, "dc.identifier.doi"))
    return Paper(
        internal_id="", title=title,
        authors=_values(metadata, "dc.contributor.author"),
        year=year, abstract=abstract,
        document_type=_first(metadata, "dc.type"),
        doi=doi, institution=_first(metadata, "dc.publisher") or "UFSCar",
        language=_first(metadata, "dc.language.iso"),
        url=f"{LANDING_URL}/{identifier}",
        landing_url=f"{LANDING_URL}/{identifier}",
        topics=_values(metadata, "dc.subject"),
        sources=["ufscar"],
        source_scores={"ufscar": max(0.2, 1.0 / (rank + 1))},
        metadata={"ufscar_id": identifier, "handle": text(item.get("handle"))},
        provenance={"ufscar": {"item_id": identifier, "type": _first(metadata, "dc.type")}},
    )


class UfscarClient:
    source = "ufscar"

    def __init__(self, *, timeout: float = 8.0, max_retries: int = 1,
                 http: JsonHttpClient | None = None) -> None:
        self.http = http or JsonHttpClient(
            BASE_URL, self.source, timeout=timeout, max_retries=max_retries,
        )

    @property
    def configured(self) -> bool:
        return True

    def search(self, query: str, *, limit: int = 10,
               year_from: int | None = None, year_to: int | None = None,
               open_access_only: bool = True) -> list[Paper]:
        payload = self.http.get_json("/discover/search/objects", params={
            "query": query, "dsoType": "item", "size": max(1, min(limit, 40)),
        })
        results = as_mapping(as_mapping(as_mapping(payload).get("_embedded")).get("searchResult"))
        records = as_mapping(results.get("_embedded")).get("objects")
        if not isinstance(records, list):
            raise SourceError(self.source, "UFSCar returned no usable items", code="invalid_payload")
        papers = [
            _record_to_paper(as_mapping(as_mapping(record).get("_embedded")).get("indexableObject"), rank)
            for rank, record in enumerate(records)
        ]
        return [paper for paper in papers if paper is not None
                and (year_from is None or paper.year is None or paper.year >= year_from)
                and (year_to is None or paper.year is None or paper.year <= year_to)]

    def get(self, identifier: str) -> Paper | None:
        item_id = identifier.strip().removeprefix("ufscar:")
        payload = self.http.get_json(f"/core/items/{item_id}")
        return _record_to_paper(as_mapping(payload))

    def close(self) -> None:
        self.http.close()


__all__ = ["UfscarClient"]
