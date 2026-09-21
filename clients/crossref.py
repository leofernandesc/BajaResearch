"""Crossref REST client used for DOI resolution and bibliographic fallback."""

from __future__ import annotations

import os
from typing import Any, Mapping
from urllib.parse import quote

from .base import SourceError, as_list, as_mapping, first_text, text, year_from_payload
from .http import USER_AGENT, JsonHttpClient
try:
    from ..models import Paper, normalize_doi
except ImportError:  # pragma: no cover - direct test imports
    from models import Paper, normalize_doi


BASE_URL = "https://api.crossref.org"


def _authors(item: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    for author in as_list(item.get("author")):
        if not isinstance(author, Mapping):
            continue
        given = text(author.get("given"))
        family = text(author.get("family"))
        literal = text(author.get("name"))
        name = " ".join(part for part in (given, family) if part) or literal
        if name:
            result.append(name)
    return result


def _year(item: Mapping[str, Any]) -> int | None:
    for key in ("published-print", "published-online", "issued", "published", "created"):
        value = year_from_payload(item.get(key))
        if value:
            return value
    return None


def _licensed_pdf(item: Mapping[str, Any]) -> str | None:
    # A Crossref link alone is not enough to claim open access. Require a
    # license record before exposing a link as open_access_url.
    licenses = as_list(item.get("license"))
    if not licenses:
        return None
    for link in as_list(item.get("link")):
        if not isinstance(link, Mapping):
            continue
        content_type = str(link.get("content-type") or "").lower()
        url = text(link.get("URL"))
        if url and ("pdf" in content_type or not content_type):
            return url
    return None


def _work_to_paper(item: Mapping[str, Any]) -> Paper | None:
    title = first_text(item.get("title"))
    doi = normalize_doi(item.get("DOI"))
    if not title and not doi:
        return None
    title = title or doi or "Untitled paper"
    url = text(item.get("URL")) or (f"https://doi.org/{doi}" if doi else None)
    oa_url = _licensed_pdf(item)
    metadata: dict[str, Any] = {"crossref_type": text(item.get("type"))}
    if as_list(item.get("license")):
        metadata["license_present"] = True
    return Paper(
        internal_id="",
        title=title,
        authors=_authors(item),
        year=_year(item),
        abstract=text(item.get("abstract")),
        venue=first_text(item.get("container-title")),
        doi=doi,
        citation_count=item.get("is-referenced-by-count"),
        url=url,
        open_access_url=oa_url,
        sources=["crossref"],
        source_scores={"crossref": 0.35},
        metadata=metadata,
    )


class CrossrefClient:
    source = "crossref"

    def __init__(
        self,
        *,
        mailto: str | None = None,
        timeout: float = 15.0,
        max_retries: int = 2,
        http: JsonHttpClient | None = None,
    ) -> None:
        self.mailto = mailto or os.getenv("CROSSREF_MAILTO") or None
        user_agent = USER_AGENT + (f"; mailto:{self.mailto}" if self.mailto else "")
        self.http = http or JsonHttpClient(
            BASE_URL,
            self.source,
            timeout=timeout,
            max_retries=max_retries,
            headers={"User-Agent": user_agent},
        )

    @property
    def configured(self) -> bool:
        return bool(self.mailto)

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
            "query.bibliographic": query,
            "rows": max(1, min(int(limit), 100)),
            "select": "DOI,title,author,published-print,published-online,issued,created,container-title,URL,is-referenced-by-count,link,license,type",
        }
        filters: list[str] = []
        if year_from is not None:
            filters.append(f"from-pub-date:{int(year_from)}-01-01")
        if year_to is not None:
            filters.append(f"until-pub-date:{int(year_to)}-12-31")
        if open_access_only:
            filters.append("has-license:true")
        if filters:
            params["filter"] = ",".join(filters)
        if self.mailto:
            params["mailto"] = self.mailto
        payload = self.http.get_json("/works", params=params)
        message = as_mapping(payload).get("message")
        items = as_mapping(message).get("items")
        if not isinstance(items, list):
            raise SourceError(
                self.source,
                "Crossref returned no usable items array",
                code="invalid_payload",
            )
        papers = [_work_to_paper(as_mapping(item)) for item in items]
        return [paper for paper in papers if paper is not None]

    def get(self, identifier: str) -> Paper | None:
        doi = normalize_doi(identifier)
        if not doi:
            return None
        params = {"mailto": self.mailto} if self.mailto else None
        payload = self.http.get_json(f"/works/{quote(doi, safe='')}", params=params)
        item = as_mapping(payload).get("message")
        return _work_to_paper(as_mapping(item))

    def close(self) -> None:
        self.http.close()


__all__ = ["CrossrefClient"]
