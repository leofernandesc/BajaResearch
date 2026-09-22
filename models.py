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
_SOURCE_PRIORITY = {
    "ufscar": 7,
    "arxiv": 2,
    "oasisbr": 6,
    "bdtd": 6,
    "openalex": 4,
    "semantic_scholar": 3,
    "crossref": 2,
    "unpaywall": 1,
}

_DOCUMENT_TYPE_ALIASES = {
    "bachelorthesis": "bachelor_thesis",
    "bachelor thesis": "bachelor_thesis",
    "undergraduate thesis": "bachelor_thesis",
    "tcc": "bachelor_thesis",
    "trabalho de conclusao de curso": "bachelor_thesis",
    "trabalho de conclusao": "bachelor_thesis",
    "trabalho final de curso": "bachelor_thesis",
    "masterthesis": "master_thesis",
    "master thesis": "master_thesis",
    "masters thesis": "master_thesis",
    "dissertacao": "master_thesis",
    "dissertation": "master_thesis",
    "doctoralthesis": "doctoral_thesis",
    "doctoral thesis": "doctoral_thesis",
    "phd thesis": "doctoral_thesis",
    "tese": "doctoral_thesis",
    "monograph": "monograph",
    "monografia": "monograph",
    "thesis": "thesis",
    "journal article": "journal_article",
    "journal-article": "journal_article",
    "article": "journal_article",
    "proceedings article": "conference_paper",
    "proceedings-article": "conference_paper",
    "conference paper": "conference_paper",
    "conference-paper": "conference_paper",
    "report": "report",
    "book chapter": "book_chapter",
    "book-chapter": "book_chapter",
}

LONG_FORM_DOCUMENT_TYPES = frozenset(
    {"bachelor_thesis", "master_thesis", "doctoral_thesis", "thesis", "monograph"}
)


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


def normalize_document_type(value: Any) -> str | None:
    """Map source-specific document types to a small auditable vocabulary."""
    raw = _clean_text(value)
    if not raw:
        return None
    normalized = normalize_title(raw)
    compact = normalized.replace(" ", "")
    if normalized in _DOCUMENT_TYPE_ALIASES:
        return _DOCUMENT_TYPE_ALIASES[normalized]
    if compact in _DOCUMENT_TYPE_ALIASES:
        return _DOCUMENT_TYPE_ALIASES[compact]
    if normalized == "tcc" or any(
        marker in normalized
        for marker in ("trabalho de conclusao", "trabalho final de curso")
    ):
        return "bachelor_thesis"
    if "bachelor" in normalized and "thesis" in normalized:
        return "bachelor_thesis"
    if ("master" in normalized or "dissert" in normalized) and (
        "thesis" in normalized or "dissert" in normalized
    ):
        return "master_thesis"
    if any(token in normalized for token in ("doctoral", "doctorate", "phd")):
        return "doctoral_thesis"
    if "thesis" in normalized:
        return "thesis"
    if "monograph" in normalized or "monografia" in normalized:
        return "monograph"
    if "conference" in normalized or "proceeding" in normalized:
        return "conference_paper"
    if "article" in normalized:
        return "journal_article"
    return normalized.replace(" ", "_")[:80]


def is_long_form_document(value: Any) -> bool:
    return normalize_document_type(value) in LONG_FORM_DOCUMENT_TYPES


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
    document_type: str | None = None
    doi: str | None = None
    openalex_id: str | None = None
    semantic_scholar_id: str | None = None
    oasisbr_id: str | None = None
    bdtd_id: str | None = None
    citation_count: int | None = None
    institution: str | None = None
    language: str | None = None
    url: str | None = None
    open_access_url: str | None = None
    landing_url: str | None = None
    full_text_url: str | None = None
    access_status: str = "unknown"
    access_evidence: dict[str, Any] = field(default_factory=dict)
    access_verified_at: str | None = None
    topics: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    source_scores: dict[str, float] = field(default_factory=dict)
    ranking_score: float | None = None
    score_details: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    ranking_version: str | None = None
    verified_url: str | None = None
    verified_open_access_url: str | None = None
    link_status: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.title = _clean_text(self.title) or "Untitled paper"
        self.doi = normalize_doi(self.doi)
        self.year = _clean_year(self.year)
        self.abstract = _clean_text(self.abstract)
        self.venue = _clean_text(self.venue)
        self.document_type = normalize_document_type(self.document_type)
        self.oasisbr_id = _clean_text(self.oasisbr_id)
        self.bdtd_id = _clean_text(self.bdtd_id)
        self.institution = _clean_text(self.institution)
        self.language = _clean_text(self.language)
        self.url = _clean_text(self.url)
        self.open_access_url = _clean_text(self.open_access_url)
        self.landing_url = _clean_text(self.landing_url) or self.url
        self.full_text_url = _clean_text(self.full_text_url)
        self.access_status = _clean_text(self.access_status) or "unknown"
        self.access_evidence = dict(self.access_evidence or {})
        self.access_verified_at = _clean_text(self.access_verified_at)
        self.verified_url = _clean_text(self.verified_url)
        self.verified_open_access_url = _clean_text(self.verified_open_access_url)
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
        self.provenance = dict(self.provenance or {})
        self.ranking_version = _clean_text(self.ranking_version)
        self.link_status = {
            str(key): str(value)
            for key, value in (self.link_status or {}).items()
            if value
        }

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
            document_type=value.get("document_type"),
            doi=value.get("doi"),
            openalex_id=value.get("openalex_id"),
            semantic_scholar_id=value.get("semantic_scholar_id"),
            oasisbr_id=value.get("oasisbr_id"),
            bdtd_id=value.get("bdtd_id"),
            citation_count=value.get("citation_count"),
            institution=value.get("institution"),
            language=value.get("language"),
            url=value.get("url"),
            open_access_url=value.get("open_access_url"),
            landing_url=value.get("landing_url"),
            full_text_url=value.get("full_text_url"),
            access_status=value.get("access_status") or "unknown",
            access_evidence=dict(value.get("access_evidence") or {}),
            access_verified_at=value.get("access_verified_at"),
            topics=list(value.get("topics") or []),
            sources=list(value.get("sources") or []),
            source_scores=dict(value.get("source_scores") or {}),
            ranking_score=value.get("ranking_score"),
            score_details=dict(value.get("score_details") or {}),
            metadata=dict(value.get("metadata") or {}),
            provenance=dict(value.get("provenance") or {}),
            ranking_version=value.get("ranking_version"),
            verified_url=value.get("verified_url"),
            verified_open_access_url=value.get("verified_open_access_url"),
            link_status=dict(value.get("link_status") or value.get("link_verification") or {}),
        )

    def public_full_text_url(self) -> str | None:
        """Return a URL only when anonymous PDF access was actually verified."""
        if self.access_status == "verified_pdf" and self.full_text_url:
            return self.full_text_url
        # Read compatibility for records verified by the v0.1 validator. New
        # searches always use ``access_status=verified_pdf``.
        if self.link_status.get("open_access_url") == "valid":
            return self.verified_open_access_url or self.open_access_url
        return None

    def candidate_full_text_urls(self) -> list[str]:
        values = [
            self.full_text_url,
            self.open_access_url,
            *list(self.metadata.get("full_text_candidates") or []),
        ]
        return list(dict.fromkeys(str(value).strip() for value in values if value))

    def to_dict(self, *, compact: bool = True) -> dict[str, Any]:
        """Serialize only normalized fields; never expose raw API payloads."""
        full_text_url = self.public_full_text_url()
        result: dict[str, Any] = {
            "internal_id": self.internal_id,
            "title": self.title,
            "authors": self.authors[:12],
            "year": self.year,
            "abstract": self.abstract if not compact else None,
            "venue": self.venue,
            "document_type": self.document_type,
            "doi": self.doi,
            "openalex_id": self.openalex_id,
            "semantic_scholar_id": self.semantic_scholar_id,
            "oasisbr_id": self.oasisbr_id,
            "bdtd_id": self.bdtd_id,
            "citation_count": self.citation_count,
            "institution": self.institution,
            "language": self.language,
            "url": full_text_url,
            "open_access_url": full_text_url,
            "full_text_url": full_text_url,
            "access_status": self.access_status,
            "access_verified_at": self.access_verified_at,
            "document_class": (
                "long_form" if is_long_form_document(self.document_type) else "article_or_other"
            ),
            "topics": self.topics[:20],
            "sources": self.sources,
            "source_scores": self.source_scores,
        }
        if compact:
            result.pop("abstract")
            if self.abstract:
                result["abstract_snippet"] = self.abstract[:700]
        if self.link_status and not compact:
            result["link_verification"] = dict(self.link_status)
        if self.access_evidence and not compact:
            result["access_evidence"] = dict(self.access_evidence)
        elif self.access_evidence:
            result["access_verification"] = {
                key: self.access_evidence[key]
                for key in ("method", "anonymous", "full_download", "page_count", "downloaded_bytes", "sha256", "pdf_magic", "pdf_eof")
                if key in self.access_evidence
            }
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
        if not compact:
            result["landing_url"] = self.landing_url
            result["candidate_full_text_urls"] = self.candidate_full_text_urls()
            result["provenance"] = self.provenance
        if self.ranking_version:
            result["ranking_version"] = self.ranking_version
        reasons: list[str] = []
        if is_long_form_document(self.document_type):
            reasons.append("long_form_document")
        if self.score_details.get("technical_relevance", 0.0) >= 0.65:
            reasons.append("strong_technical_match")
        if self.score_details.get("application_context", 0.0) >= 0.70:
            reasons.append("baja_formula_offroad_context")
        if len(set(self.sources)) > 1:
            reasons.append("confirmed_by_multiple_sources")
        if self.access_status == "verified_pdf":
            reasons.append("verified_free_pdf")
        if reasons:
            result["relevance_reasons"] = reasons
        return result

    def to_storage_dict(self) -> dict[str, Any]:
        """Serialize all selected metadata needed to reconstruct the record."""
        result = self.to_dict(compact=False)
        result.update(
            {
                "url": self.url,
                "open_access_url": self.open_access_url,
                "landing_url": self.landing_url,
                "full_text_url": self.full_text_url,
                "access_status": self.access_status,
            }
        )
        return result


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
    if paper.oasisbr_id:
        return f"oasisbr:{paper.oasisbr_id}"
    if paper.bdtd_id:
        return f"bdtd:{paper.bdtd_id}"
    identity = f"{normalize_title(paper.title)}|{paper.year or ''}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"paper:{digest}"


def _identity_ids(paper: Paper) -> set[str]:
    keys: set[str] = set()
    if paper.internal_id:
        keys.add(f"internal:{paper.internal_id}")
    if paper.doi:
        keys.add(f"doi:{paper.doi}")
    if paper.openalex_id:
        keys.add(f"openalex:{paper.openalex_id}")
    if paper.semantic_scholar_id:
        keys.add(f"s2:{paper.semantic_scholar_id}")
    if paper.oasisbr_id:
        keys.add(f"oasisbr:{paper.oasisbr_id}")
    if paper.bdtd_id:
        keys.add(f"bdtd:{paper.bdtd_id}")
    return keys


def _title_year_key(paper: Paper) -> str | None:
    title = normalize_title(paper.title)
    if not title:
        return None
    # Exact normalized titles are safe enough to collapse when a repository
    # omitted the year. Fuzzy matching still requires the same known year.
    return f"{title}|{paper.year if paper.year is not None else 'unknown'}"


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
    provenance = dict(left.provenance)
    provenance.update(right.provenance)
    access_evidence = dict(left.access_evidence)
    access_evidence.update(right.access_evidence)
    verified_access = right if right.access_status == "verified_pdf" else left
    if left.access_status != "verified_pdf" and right.access_status != "verified_pdf":
        verified_access = right if prefer_right else left
    merged = Paper(
        internal_id=left.internal_id or right.internal_id,
        title=choose_text(left.title, right.title) or "Untitled paper",
        authors=_unique_strings([*left.authors, *right.authors]),
        year=choose_value(left.year, right.year),
        abstract=choose_text(left.abstract, right.abstract),
        venue=choose_text(left.venue, right.venue),
        document_type=choose_text(left.document_type, right.document_type),
        doi=left.doi or right.doi,
        openalex_id=left.openalex_id or right.openalex_id,
        semantic_scholar_id=left.semantic_scholar_id or right.semantic_scholar_id,
        oasisbr_id=left.oasisbr_id or right.oasisbr_id,
        bdtd_id=left.bdtd_id or right.bdtd_id,
        citation_count=max(
            [count for count in (left.citation_count, right.citation_count) if count is not None],
            default=None,
        ),
        institution=choose_text(left.institution, right.institution),
        language=choose_text(left.language, right.language),
        url=choose_text(left.url, right.url),
        open_access_url=left.open_access_url or right.open_access_url,
        landing_url=choose_text(left.landing_url, right.landing_url),
        full_text_url=verified_access.full_text_url,
        access_status=verified_access.access_status,
        access_evidence=access_evidence,
        access_verified_at=verified_access.access_verified_at,
        topics=_unique_strings([*left.topics, *right.topics]),
        sources=_unique_strings([*left.sources, *right.sources]),
        source_scores=source_scores,
        ranking_score=right.ranking_score if right.ranking_score is not None else left.ranking_score,
        score_details=dict(right.score_details or left.score_details),
        metadata=metadata,
        provenance=provenance,
        ranking_version=right.ranking_version or left.ranking_version,
        verified_url=left.verified_url or right.verified_url,
        verified_open_access_url=left.verified_open_access_url or right.verified_open_access_url,
        link_status={**left.link_status, **right.link_status},
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
        document_type=value("document_type"),
        doi=value("doi"),
        openalex_id=value("openalex_id"),
        semantic_scholar_id=value("semantic_scholar_id"),
        oasisbr_id=value("oasisbr_id"),
        bdtd_id=value("bdtd_id"),
        authors=loads("authors_json", []),
        citation_count=value("citation_count"),
        institution=value("institution"),
        language=value("language"),
        url=value("url"),
        open_access_url=value("open_access_url"),
        landing_url=value("landing_url"),
        full_text_url=value("full_text_url"),
        access_status=value("access_status", "unknown"),
        access_evidence=loads("access_evidence_json", {}),
        access_verified_at=value("access_verified_at"),
        topics=loads("topics_json", []),
        sources=loads("sources_json", []),
        source_scores=loads("source_scores_json", {}),
        metadata=loads("metadata_json", {}),
        provenance=loads("provenance_json", {}),
        ranking_version=value("ranking_version"),
        ranking_score=value("ranking_score"),
        score_details=loads("score_details_json", {}),
        verified_url=value("verified_url"),
        verified_open_access_url=value("verified_open_access_url"),
        link_status=loads("link_status_json", {}),
    )


__all__ = [
    "Paper",
    "deduplicate_papers",
    "make_internal_id",
    "merge_papers",
    "is_long_form_document",
    "normalize_doi",
    "normalize_document_type",
    "normalize_title",
    "paper_from_storage",
]
