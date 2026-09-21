"""Normalized internal models and identity helpers.

External APIs use different field names and identifier conventions.  This
module is the only place where those records become the compact, source-
independent representation exposed to Hermes.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from typing import Any, Iterable, Mapping
from urllib.parse import unquote


_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
_DOI_PREFIX_RE = re.compile(
    r"^(?:https?://)?(?:dx\.)?doi\.org/|^doi:\s*", re.IGNORECASE
)
_SOURCE_PRIORITY = {"openalex": 3, "semantic_scholar": 2, "crossref": 1}


def normalize_doi(value: Any) -> str | None:
    """Return a canonical DOI or ``None`` for a missing/invalid value.

    Canonical values are lowercase strings such as ``10.1234/example``.
    Common URL and ``doi:`` forms are accepted, while trailing citation
    punctuation is removed without inventing a DOI.
    """
    if value is None:
        return None
    text = unquote(str(value)).strip().strip("<>\"'()")
    if not text:
        return None
    text = _DOI_PREFIX_RE.sub("", text).strip()
    text = re.sub(r"\s+", "", text).lower()
    text = text.rstrip(".,;:!?.]}>")
    text = text.rstrip(")")
    if _DOI_RE.fullmatch(text) is None:
        return None
    return text


def normalize_title(value: Any) -> str:
    """Normalize a title for deterministic matching, not for display."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_year(value: Any) -> int | None:
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if 1000 <= year <= 2200 else None


def _author_name(value: Any) -> str | None:
    if isinstance(value, Mapping):
        name = value.get("name") or value.get("display_name")
        if name:
            return _clean_text(name)
        given = _clean_text(value.get("given"))
        family = _clean_text(value.get("family"))
        return " ".join(part for part in (given, family) if part) or None
    return _clean_text(value)


def _unique_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_text(value)
        if not text:
            continue
        key = normalize_title(text)
        if key and key not in seen:
            seen.add(key)
            result.append(text)
    return result


@dataclass
class Paper:
    """A normalized paper record independent of any academic API."""

    internal_id: str
    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    abstract: str | None = None
    venue: str | None = None
    doi: str | None = None
    openalex_id: str | None = None
    semantic_scholar_id: str | None = None
    citation_count: int | None = None
    url: str | None = None
    open_access_url: str | None = None
    topics: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    source_scores: dict[str, float] = field(default_factory=dict)
    ranking_score: float | None = None
    score_details: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.title = _clean_text(self.title) or "Untitled paper"
        self.doi = normalize_doi(self.doi)
        self.year = _clean_year(self.year)
        self.abstract = _clean_text(self.abstract)
        self.venue = _clean_text(self.venue)
        self.url = _clean_text(self.url)
        self.open_access_url = _clean_text(self.open_access_url)
        self.authors = _unique_strings(self.authors)
        self.topics = _unique_strings(self.topics)
        self.sources = _unique_strings(self.sources)
        self.source_scores = {
            str(key): float(value)
            for key, value in self.source_scores.items()
            if _is_number(value)
        }
        if self.citation_count is not None:
            try:
                self.citation_count = max(0, int(self.citation_count))
            except (TypeError, ValueError):
                self.citation_count = None
        self.metadata = dict(self.metadata or {})

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Paper":
        """Build a paper from storage or a test fixture defensively."""
        return cls(
            internal_id=str(value.get("internal_id") or value.get("id") or ""),
            title=str(value.get("title") or "Untitled paper"),
            authors=list(value.get("authors") or []),
            year=value.get("year"),
            abstract=value.get("abstract"),
            venue=value.get("venue"),
            doi=value.get("doi"),
            openalex_id=value.get("openalex_id"),
            semantic_scholar_id=value.get("semantic_scholar_id"),
            citation_count=value.get("citation_count"),
            url=value.get("url"),
            open_access_url=value.get("open_access_url"),
            topics=list(value.get("topics") or []),
            sources=list(value.get("sources") or []),
            source_scores=dict(value.get("source_scores") or {}),
            ranking_score=value.get("ranking_score"),
            score_details=dict(value.get("score_details") or {}),
            metadata=dict(value.get("metadata") or {}),
        )

    def to_dict(self, *, compact: bool = True) -> dict[str, Any]:
        """Serialize only normalized fields; never expose raw API payloads."""
        result: dict[str, Any] = {
            "internal_id": self.internal_id,
            "title": self.title,
            "authors": self.authors[:12],
            "year": self.year,
            "abstract": self.abstract if not compact else None,
            "venue": self.venue,
            "doi": self.doi,
            "openalex_id": self.openalex_id,
            "semantic_scholar_id": self.semantic_scholar_id,
            "citation_count": self.citation_count,
            "url": self.url,
            "open_access_url": self.open_access_url,
            "topics": self.topics[:20],
            "sources": self.sources,
            "source_scores": self.source_scores,
        }
        if compact:
            result.pop("abstract")
            if self.abstract:
                result["abstract_snippet"] = self.abstract[:700]
        if self.ranking_score is not None:
            result["ranking_score"] = round(float(self.ranking_score), 6)
        if self.score_details:
            result["score_details"] = {
                key: round(float(value), 6)
                for key, value in self.score_details.items()
                if _is_number(value)
            }
        if self.metadata and not compact:
            result["metadata"] = self.metadata
        return result

    def to_storage_dict(self) -> dict[str, Any]:
        """Serialize all selected metadata needed to reconstruct the record."""
        return self.to_dict(compact=False)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def make_internal_id(paper: Paper) -> str:
    """Create a stable internal id using the strongest known identity."""
    if paper.doi:
        return f"doi:{paper.doi}"
    if paper.openalex_id:
        return f"openalex:{paper.openalex_id}"
    if paper.semantic_scholar_id:
        return f"s2:{paper.semantic_scholar_id}"
    identity = f"{normalize_title(paper.title)}|{paper.year or ''}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"paper:{digest}"


def _identity_ids(paper: Paper) -> set[str]:
    keys: set[str] = set()
    if paper.doi:
        keys.add(f"doi:{paper.doi}")
    if paper.openalex_id:
        keys.add(f"openalex:{paper.openalex_id}")
    if paper.semantic_scholar_id:
        keys.add(f"s2:{paper.semantic_scholar_id}")
    return keys


def _title_year_key(paper: Paper) -> str | None:
    title = normalize_title(paper.title)
    if not title or paper.year is None:
        return None
    return f"{title}|{paper.year}"


def _best_source(paper: Paper) -> str:
    return max(
        paper.sources or [""],
        key=lambda source: (_SOURCE_PRIORITY.get(source, 0), source),
    )


def merge_papers(left: Paper, right: Paper) -> Paper:
    """Merge complementary records without dropping source provenance."""
    left_priority = _SOURCE_PRIORITY.get(_best_source(left), 0)
    right_priority = _SOURCE_PRIORITY.get(_best_source(right), 0)
    prefer_right = right_priority > left_priority

    def choose_text(first: str | None, second: str | None) -> str | None:
        if first and second:
            return second if prefer_right else first
        return first or second

    def choose_value(first: Any, second: Any) -> Any:
        if first is None or first == "":
            return second
        if second is None or second == "":
            return first
        return second if prefer_right else first

    source_scores = dict(left.source_scores)
    source_scores.update(right.source_scores)
    metadata = dict(left.metadata)
    metadata.update(right.metadata)
    merged = Paper(
        internal_id=left.internal_id or right.internal_id,
        title=choose_text(left.title, right.title) or "Untitled paper",
        authors=_unique_strings([*left.authors, *right.authors]),
        year=choose_value(left.year, right.year),
        abstract=choose_text(left.abstract, right.abstract),
        venue=choose_text(left.venue, right.venue),
        doi=left.doi or right.doi,
        openalex_id=left.openalex_id or right.openalex_id,
        semantic_scholar_id=left.semantic_scholar_id or right.semantic_scholar_id,
        citation_count=max(
            [count for count in (left.citation_count, right.citation_count) if count is not None],
            default=None,
        ),
        url=choose_text(left.url, right.url),
        open_access_url=left.open_access_url or right.open_access_url,
        topics=_unique_strings([*left.topics, *right.topics]),
        sources=_unique_strings([*left.sources, *right.sources]),
        source_scores=source_scores,
        ranking_score=right.ranking_score if right.ranking_score is not None else left.ranking_score,
        score_details=dict(right.score_details or left.score_details),
        metadata=metadata,
    )
    merged.internal_id = make_internal_id(merged)
    return merged


def _fuzzy_match(left: Paper, right: Paper) -> bool:
    left_title = normalize_title(left.title)
    right_title = normalize_title(right.title)
    if len(left_title) < 32 or len(right_title) < 32:
        return False
    if left.year is None or right.year is None or left.year != right.year:
        return False
    ratio = SequenceMatcher(None, left_title, right_title).ratio()
    left_tokens = set(left_title.split())
    right_tokens = set(right_title.split())
    union = left_tokens | right_tokens
    overlap = len(left_tokens & right_tokens) / len(union) if union else 0.0
    return ratio >= 0.95 and overlap >= 0.86


def deduplicate_papers(papers: Iterable[Paper]) -> list[Paper]:
    """Deduplicate in DOI/known-id/title-year/fuzzy order.

    A DOI or known academic id takes precedence. Fuzzy matching is deliberately
    conservative and only runs for long titles with the same year.
    """
    result: list[Paper] = []
    id_index: dict[str, int] = {}
    title_index: dict[str, int] = {}

    for original in papers:
        paper = original if isinstance(original, Paper) else Paper.from_dict(original)
        if not paper.title or paper.title == "Untitled paper":
            continue
        candidate_index: int | None = None
        ids = _identity_ids(paper)
        if ids:
            for key in ids:
                if key in id_index:
                    candidate_index = id_index[key]
                    break
        title_key = _title_year_key(paper)
        if candidate_index is None and title_key is not None:
            candidate_index = title_index.get(title_key)
        if candidate_index is None and not ids:
            for index, existing in enumerate(result):
                if _fuzzy_match(existing, paper):
                    candidate_index = index
                    break

        if candidate_index is None:
            paper.internal_id = make_internal_id(paper)
            candidate_index = len(result)
            result.append(paper)
        else:
            result[candidate_index] = merge_papers(result[candidate_index], paper)

        merged = result[candidate_index]
        for key in _identity_ids(merged):
            id_index[key] = candidate_index
        merged_title_key = _title_year_key(merged)
        if merged_title_key is not None:
            title_index[merged_title_key] = candidate_index

    return result


def paper_from_storage(row: Mapping[str, Any]) -> Paper:
    """Reconstruct a paper from a SQLite row mapping."""
    def value(name: str, default: Any = None) -> Any:
        try:
            return row[name]
        except (KeyError, IndexError, TypeError):
            return default

    def loads(name: str, default: Any) -> Any:
        raw = value(name)
        if raw in (None, ""):
            return default
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return default

    return Paper(
        internal_id=str(value("internal_id") or ""),
        title=str(value("title") or "Untitled paper"),
        abstract=value("abstract"),
        year=value("year"),
        venue=value("venue"),
        doi=value("doi"),
        openalex_id=value("openalex_id"),
        semantic_scholar_id=value("semantic_scholar_id"),
        authors=loads("authors_json", []),
        citation_count=value("citation_count"),
        url=value("url"),
        open_access_url=value("open_access_url"),
        topics=loads("topics_json", []),
        sources=loads("sources_json", []),
        source_scores=loads("source_scores_json", {}),
        metadata=loads("metadata_json", {}),
        ranking_score=value("ranking_score"),
        score_details=loads("score_details_json", {}),
    )


__all__ = [
    "Paper",
    "deduplicate_papers",
    "make_internal_id",
    "merge_papers",
    "normalize_doi",
    "normalize_title",
    "paper_from_storage",
]
