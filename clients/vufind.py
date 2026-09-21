"""Shared normalizer for IBICT VuFind JSON search APIs."""

from __future__ import annotations

from typing import Any, Mapping

from .base import SourceError, as_list, as_mapping, text
from .http import JsonHttpClient
try:
    from ..models import Paper, normalize_doi
except ImportError:  # pragma: no cover - direct test imports
    from models import Paper, normalize_doi


def _names(authors: Any) -> list[str]:
    value = as_mapping(authors)
    primary = value.get("primary")
    result: list[str] = []
    if isinstance(primary, Mapping):
        result.extend(str(name).strip() for name in primary if str(name).strip())
    elif isinstance(primary, list):
        result.extend(str(name).strip() for name in primary if str(name).strip())
    if not result:
        corporate = value.get("corporate")
        if isinstance(corporate, Mapping):
            result.extend(str(name).strip() for name in corporate if str(name).strip())
        elif isinstance(corporate, list):
            result.extend(str(name).strip() for name in corporate if str(name).strip())
    return result


def _urls(record: Mapping[str, Any]) -> tuple[str | None, list[str]]:
    landing: str | None = None
    pdfs: list[str] = []
    for item in as_list(record.get("urls")):
        candidate = text(as_mapping(item).get("url") if isinstance(item, Mapping) else item)
        if not candidate:
            continue
        if candidate.casefold().split("?", 1)[0].endswith(".pdf"):
            pdfs.append(candidate)
        elif landing is None:
            landing = candidate
    return landing, list(dict.fromkeys(pdfs))


def record_to_paper(record: Mapping[str, Any], *, source: str, rank: int = 0) -> Paper | None:
    title = text(record.get("title"))
    record_id = text(record.get("id"))
    if not title or not record_id:
        return None
    formats = [str(value) for value in as_list(record.get("formats")) if text(value)]
    languages = [str(value) for value in as_list(record.get("languages")) if text(value)]
    subjects = [str(value) for value in as_list(record.get("subjects")) if text(value)]
    landing_url, pdfs = _urls(record)
    oai_identifier = text(record.get("oai_identifier_st"))
    doi = normalize_doi(record.get("doi") or record.get("DOI"))
    denominator = max(1, rank + 1)
    source_score = max(0.35, 1.0 / denominator)
    metadata: dict[str, Any] = {
        "full_text_candidates": pdfs,
        "oai_identifier": oai_identifier,
        "institution_code": record_id.split("_", 1)[0],
        "vufind_formats": formats,
    }
    identifier_fields = {
        "oasisbr_id": record_id if source == "oasisbr" else None,
        "bdtd_id": record_id if source == "bdtd" else None,
    }
    return Paper(
        internal_id="",
        title=title,
        authors=_names(record.get("authors")),
        document_type=formats[0] if formats else None,
        doi=doi,
        language=languages[0] if languages else None,
        url=landing_url,
        landing_url=landing_url,
        open_access_url=pdfs[0] if pdfs else None,
        topics=subjects,
        sources=[source],
        source_scores={source: source_score},
        metadata=metadata,
        provenance={
            source: {
                "record_id": record_id,
                "oai_identifier": oai_identifier,
                "landing_url": landing_url,
            }
        },
        **identifier_fields,
    )


class VuFindClient:
    """Minimal client for the public JSON endpoints used by Oasisbr/BDTD."""

    source = "vufind"

    def __init__(
        self,
        base_url: str,
        source: str,
        *,
        timeout: float = 8.0,
        max_retries: int = 1,
        http: JsonHttpClient | None = None,
    ) -> None:
        self.source = source
        self.http = http or JsonHttpClient(
            base_url, source, timeout=timeout, max_retries=max_retries
        )

    @property
    def configured(self) -> bool:
        return True

    def _papers(self, payload: Any) -> list[Paper]:
        root = as_mapping(payload)
        records = root.get("records")
        if records is None and root.get("status") == "OK" and int(root.get("resultCount") or 0) == 0:
            return []
        if not isinstance(records, list):
            raise SourceError(
                self.source,
                f"{self.source} returned no usable records array",
                code="invalid_payload",
            )
        papers = [
            record_to_paper(as_mapping(record), source=self.source, rank=index)
            for index, record in enumerate(records)
        ]
        return [paper for paper in papers if paper is not None]

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        year_from: int | None = None,
        year_to: int | None = None,
        open_access_only: bool = True,
    ) -> list[Paper]:
        # Free full-text availability is deliberately decided later by byte
        # verification; the repository index itself is only discovery evidence.
        payload = self.http.get_json(
            "/search",
            params={
                "lookfor": query,
                "type": "AllFields",
                "sort": "relevance",
                "limit": max(1, min(int(limit), 100)),
                "page": 1,
            },
        )
        papers = self._papers(payload)
        return [
            paper
            for paper in papers
            if (year_from is None or paper.year is None or paper.year >= year_from)
            and (year_to is None or paper.year is None or paper.year <= year_to)
        ]

    def get(self, identifier: str) -> Paper | None:
        raw = identifier.strip()
        prefix = f"{self.source}:"
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
        payload = self.http.get_json("/record", params={"id": raw})
        papers = self._papers(payload)
        return papers[0] if papers else None

    def close(self) -> None:
        self.http.close()


__all__ = ["VuFindClient", "record_to_paper"]
