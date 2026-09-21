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
_EXPLICIT_CONTEXT_TERMS = {
    "baja sae", "formula sae", "formula student", "off road",
    "offroad", "atv", "motorsport",
}
_GENERAL_VEHICLE_TERMS = {"automotive", "vehicle dynamics", "vehicle"}
_THESIS_TERMS = {
    "thesis", "dissertation", "tcc", "monograph", "monografia",
    "undergraduate thesis", "master thesis", "doctoral thesis",
}
_REPOSITORY_TERMS = {
    "repository", "institutional repository", "repositorio", "dspace",
    "etd", "eprints", "scholarworks", "handle.net", "university archive",
}
_ELECTRIC_VEHICLE_TERMS = {
    "electric vehicle", "electric vehicles", "battery electric vehicle",
    "plug in hybrid", "plug in hybrid vehicle", "hybrid electric vehicle",
    "hybrid vehicle", "fuel cell vehicle", "electric mobility", "electric car",
    "electrified vehicle", "ev", "veiculo eletrico", "veiculos eletricos",
    "carro eletrico", "mobilidade eletrica", "veiculo hibrido",
    "veiculos hibridos", "celula a combustivel", "mobil listrik",
    "kendaraan listrik", "electric baja", "baja electric", "electric atv",
    "electric off road", "electric offroad", "electric formula sae",
    "electric formula student", "electric powertrain", "electric drivetrain",
    "electric propulsion", "battery powered vehicle", "battery vehicle",
    "hybrid baja", "hybrid atv", "vehiculo electrico", "vehiculos electricos",
    "coche electrico", "movilidad electrica", "vehiculo hibrido",
    "vehiculos hibridos", "electrofahrzeug", "elektrofahrzeuge", "elektroauto",
    "hybridfahrzeug", "vehicule electrique", "vehicules electriques",
    "voiture electrique", "vehicule hybride", "kenderaan elektrik",
}
_ELECTRIC_VEHICLE_SUPPORT_TERMS = {
    "battery", "charging", "charger", "powertrain", "fuel cell", "traction",
    "state of charge", "energy management",
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


def _contains_term(haystack: str, term: str) -> bool:
    """Match normalized words/phrases without substring false positives."""
    needle = normalize_title(term)
    padded = f" {haystack} "
    return bool(needle) and f" {needle} " in padded


def electric_vehicle_signal(paper: Paper) -> float:
    """Detect papers primarily about EV/hybrid/fuel-cell vehicles.

    A generic mention of electricity is not enough. Strong title/topic/venue
    evidence, or repeated/supporting evidence in the abstract, is required so
    ordinary vehicle-electronics papers are not discarded accidentally.
    """
    title_topics = normalize_title(
        " ".join([paper.title, paper.venue or "", *paper.topics])
    )
    if any(_contains_term(title_topics, term) for term in _ELECTRIC_VEHICLE_TERMS):
        return 1.0

    abstract = normalize_title(paper.abstract or "")
    matches = sum(
        1 for term in _ELECTRIC_VEHICLE_TERMS if _contains_term(abstract, term)
    )
    support = sum(
        1
        for term in _ELECTRIC_VEHICLE_SUPPORT_TERMS
        if _contains_term(abstract, term)
    )
    if matches >= 2 or (matches >= 1 and support >= 1):
        return 0.85
    return 0.0


def is_electric_vehicle_paper(paper: Paper) -> bool:
    """Return whether the record should be excluded by the BAJA default."""
    return electric_vehicle_signal(paper) >= 0.75


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
    haystack = normalize_title(
        " ".join([paper.title, paper.abstract or "", *paper.topics])
    )
    explicit_matches = sum(
        1 for term in _EXPLICIT_CONTEXT_TERMS if _contains_term(haystack, term)
    )
    if explicit_matches:
        return min(1.0, 0.8 + 0.1 * max(0, explicit_matches - 1))
    # A generic mention such as “electric vehicle charging” in an abstract is
    # not enough to make a paper Baja/vehicle-contextual. General vehicle
    # signals must be visible in the title, venue, or topics.
    title_topics = normalize_title(
        " ".join([paper.title, paper.venue or "", *paper.topics])
    )
    general_matches = sum(
        1
        for term in _GENERAL_VEHICLE_TERMS
        if _contains_term(title_topics, term)
    )
    return min(0.6, 0.3 * general_matches)


def _thesis_signal(paper: Paper) -> float:
    """Estimate whether a record is a thesis-like, repository-hosted work."""
    document_type = normalize_title(
        " ".join(
            value
            for value in (
                paper.document_type,
                str(paper.metadata.get("openalex_type") or ""),
                str(paper.metadata.get("crossref_type") or ""),
            )
            if value
        )
    )
    haystack = normalize_title(
        " ".join(
            [paper.title, paper.venue or "", paper.url or "", *paper.topics]
        )
    )
    if any(term in document_type for term in ("thesis", "dissertation", "monograph")):
        return 1.0
    if any(term in haystack for term in _THESIS_TERMS):
        return 1.0
    if any(term in haystack for term in _REPOSITORY_TERMS):
        return 0.45
    return 0.0


def rank_papers(
    papers: Iterable[Paper],
    queries: Iterable[str],
    *,
    limit: int | None = None,
    current_year: int | None = None,
    prefer_theses: bool = False,
    require_context: bool = False,
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
            "thesis_signal": _thesis_signal(paper) if prefer_theses else 0.0,
        }
        if prefer_theses:
            score = (
                0.47 * components["query_relevance"]
                + 0.10 * components["source_relevance"]
                + 0.08 * components["multi_source"]
                + 0.07 * components["citation_signal"]
                + 0.03 * components["recency_signal"]
                + 0.07 * components["context_signal"]
                + 0.18 * components["thesis_signal"]
            )
        else:
            score = (
                0.55 * components["query_relevance"]
                + 0.12 * components["source_relevance"]
                + 0.10 * components["multi_source"]
                + 0.10 * components["citation_signal"]
                + 0.05 * components["recency_signal"]
                + 0.08 * components["context_signal"]
            )
        if require_context and components["context_signal"] == 0.0:
            # Keep general-domain fallback papers available, but make a paper
            # with no Baja/vehicle context lose to an otherwise comparable
            # contextual result.
            score *= 0.55
            components["context_gate"] = 0.55
        else:
            components["context_gate"] = 1.0
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


__all__ = ["electric_vehicle_signal", "is_electric_vehicle_paper", "rank_papers"]
