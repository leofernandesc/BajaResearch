"""Deterministic, inspectable ranking performed before the Hermes LLM."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import math
from typing import Iterable

try:
    from .models import Paper, normalize_title
except ImportError:  # pragma: no cover - direct test imports
    from models import Paper, normalize_title


_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in",
    "into", "is", "of", "on", "or", "the", "to", "with", "da", "das", "de",
    "do", "dos", "e", "em", "para", "por", "um", "uma", "que", "na", "no",
}
_CONTEXT_TERMS = {
    "baja", "baja sae", "formula sae", "formula student", "off road",
    "offroad", "atv", "automotive", "vehicle dynamics", "motorsport",
}


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in normalize_title(value).split()
        if len(token) > 1 and token not in _STOPWORDS
    }


def _coverage(needles: set[str], haystack: str) -> float:
    if not needles:
        return 0.0
    present = _tokens(haystack)
    return len(needles & present) / len(needles)


def _query_relevance(paper: Paper, queries: Iterable[str]) -> float:
    title = normalize_title(paper.title)
    abstract = paper.abstract or ""
    topics = " ".join(paper.topics)
    best = 0.0
    for query in queries:
        normalized_query = normalize_title(query)
        query_tokens = _tokens(query)
        title_score = _coverage(query_tokens, title)
        abstract_score = _coverage(query_tokens, abstract)
        topic_score = _coverage(query_tokens, topics)
        phrase_bonus = 0.18 if normalized_query and normalized_query in title else 0.0
        candidate = min(1.0, 0.58 * title_score + 0.27 * abstract_score + 0.15 * topic_score + phrase_bonus)
        best = max(best, candidate)
    return best


def _source_relevance(paper: Paper) -> float:
    values: list[float] = []
    for value in paper.source_scores.values():
        try:
            numeric = max(0.0, float(value))
        except (TypeError, ValueError):
            continue
        values.append(numeric if numeric <= 1.0 else numeric / (1.0 + numeric))
    return sum(values) / len(values) if values else (0.5 if paper.sources else 0.0)


def _citation_signal(paper: Paper, max_citations: int) -> float:
    if not paper.citation_count or max_citations <= 0:
        return 0.0
    return min(1.0, math.log1p(paper.citation_count) / math.log1p(max_citations))


def _recency_signal(paper: Paper, current_year: int) -> float:
    if paper.year is None:
        return 0.0
    age = max(0, current_year - paper.year)
    return max(0.0, 1.0 - min(age, 40) / 40.0)


def _context_signal(paper: Paper) -> float:
    haystack = normalize_title(" ".join([paper.title, paper.abstract or "", *paper.topics]))
    matches = sum(1 for term in _CONTEXT_TERMS if normalize_title(term) in haystack)
    return min(1.0, matches / 2.0)


def rank_papers(
    papers: Iterable[Paper],
    queries: Iterable[str],
    *,
    limit: int | None = None,
    current_year: int | None = None,
) -> list[Paper]:
    """Rank papers with fixed weights and retain component scores.

    Query relevance carries most of the score. Citations are logarithmic and
    weighted at 0.10, so a highly cited but off-topic paper cannot dominate a
    directly matching engineering paper.
    """
    query_list = [str(query).strip() for query in queries if str(query).strip()]
    current_year = current_year or datetime.now().year
    values = list(papers)
    max_citations = max((paper.citation_count or 0 for paper in values), default=0)
    ranked: list[Paper] = []
    for paper in values:
        components = {
            "query_relevance": _query_relevance(paper, query_list),
            "source_relevance": _source_relevance(paper),
            "multi_source": min(1.0, max(0, len(set(paper.sources)) - 1) / 2.0),
            "citation_signal": _citation_signal(paper, max_citations),
            "recency_signal": _recency_signal(paper, current_year),
            "context_signal": _context_signal(paper),
        }
        score = (
            0.55 * components["query_relevance"]
            + 0.12 * components["source_relevance"]
            + 0.10 * components["multi_source"]
            + 0.10 * components["citation_signal"]
            + 0.05 * components["recency_signal"]
            + 0.08 * components["context_signal"]
        )
        ranked.append(replace(paper, ranking_score=score, score_details=components))

    ranked.sort(
        key=lambda paper: (
            -(paper.ranking_score or 0.0),
            -(paper.citation_count or 0),
            normalize_title(paper.title),
            paper.internal_id,
        )
    )
    return ranked[:limit] if limit is not None else ranked


__all__ = ["rank_papers"]
