"""Deterministic quality gates and ranking before Hermes sees any result."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import math
from typing import Iterable

try:
    from .models import Paper, is_long_form_document, normalize_title
except ImportError:  # pragma: no cover
    from models import Paper, is_long_form_document, normalize_title


RANKING_VERSION = "6"
# An exact mention in an abstract contributes 0.25. Do not throw away a
# repository TCC merely because its title describes the broader subsystem.
TECHNICAL_RELEVANCE_THRESHOLD = 0.25
FOCUS_RELEVANCE_THRESHOLD = 0.15
APPLICATION_CONTEXT_THRESHOLD = 0.70

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in",
    "into", "is", "of", "on", "or", "the", "to", "with", "da", "das", "de",
    "do", "dos", "e", "em", "para", "por", "um", "uma", "que", "na", "no",
    "sobre", "como", "using", "use", "study", "paper", "work", "research",
}
_CONTEXT_TOKENS = {
    "baja", "sae", "formula", "student", "mini", "off", "road", "offroad",
    "vehicle", "vehicles", "veiculo", "veiculos", "automotive", "motorsport",
    "terrain", "all", "atv",
}
_GENERIC_TECHNICAL_TOKENS = {
    "analysis", "analise", "optimization", "optimisation", "otimizacao",
    "design", "project", "projeto", "development", "desenvolvimento", "model",
    "modeling", "modelling", "modelagem", "performance", "desempenho", "system",
    "systems", "sistema", "sistemas", "method", "methods", "metodo", "simulation",
    "simulacao", "evaluation", "avaliacao", "experimental", "numerical", "study",
    "thesis", "dissertation", "monograph", "tcc", "repository", "institutional",
}
_TECHNICAL_ALIASES = {
    "electronics": {
        "electronic", "eletronica", "eletronico", "telemetry", "sensor",
        "sensors", "embedded", "can", "acquisition", "instrumentation",
        "microcontroller",
    },
    "eletronica": {
        "electronic", "electronics", "eletronico", "telemetria", "sensor",
        "sensores", "embarcado", "can",
        "aquisicao", "instrumentacao", "microcontrolador",
    },
    "telemetry": {
        "telematics", "telemetria", "telemetrias", "data", "dados",
        "acquisition", "aquisicao", "sensor", "sensors", "sensores",
    },
    "data": {"dados"},
    "acquisition": {"aquisicao", "aquisicoes"},
    "sensor": {"sensores"},
    "sensors": {"sensor", "sensores"},
    "embedded": {"embarcado", "embarcada", "embarcados"},
    "can": {"can"},
    "suspension": {"suspensao", "damper", "damping", "shock", "wishbone", "camber", "toe"},
    "suspensao": {"suspension", "amortecedor", "amortecimento", "bandeja", "cambagem", "convergencia"},
    "chassis": {"chassi", "frame", "spaceframe", "rollcage", "structure", "structural", "estrutural"},
    "chassi": {"chassis", "estrutura", "estrutural", "gaiola"},
    "geometry": {"geometria", "geometric"},
    "finite": {"finito", "finitos"},
    "element": {"elemento", "elementos"},
    "structural": {"estrutura", "estrutural"},
    "transmission": {"transmissao"},
    "brake": {"brakes", "braking", "caliper", "disc"},
    "freio": {"freios", "frenagem", "pinça", "disco"},
    "telemetria": {"dados", "aquisicao", "sensor", "sensores"},
}
_STRONG_CONTEXT_TERMS = {
    "baja sae", "sae baja", "mini baja", "formula sae", "formula student",
    "off road", "offroad", "all terrain vehicle", "all terrain vehicles", "atv",
}
_DIRECT_COMPETITION_TERMS = {"baja sae", "sae baja", "mini baja", "formula sae", "formula student"}
_MOTORSPORT_TERMS = {"motorsport", "race car", "racing car", "racing vehicle", "competition vehicle"}
_GENERAL_VEHICLE_TERMS = {"automotive", "vehicle dynamics", "ground vehicle"}
_ELECTRIC_VEHICLE_TERMS = {
    "electric vehicle", "electric vehicles", "battery electric vehicle",
    "plug in hybrid", "plug in hybrid vehicle", "hybrid electric vehicle",
    "hybrid vehicle", "fuel cell vehicle", "electric mobility", "electric car",
    "electrified vehicle", "ev", "veiculo eletrico", "veiculos eletricos",
    "carro eletrico", "mobilidade eletrica", "veiculo hibrido", "veiculos hibridos",
    "celula a combustivel", "mobil listrik", "kendaraan listrik", "electric baja",
    "baja electric", "electric atv", "electric off road", "electric offroad",
    "electric formula sae", "formula sae electric", "formula student electric",
    "electric formula student", "electric powertrain", "electric drivetrain",
    "electric propulsion", "battery powered vehicle", "battery vehicle", "hybrid baja",
    "baja hybrid", "hybrid atv", "vehiculo electrico", "vehiculos electricos",
    "coche electrico", "movilidad electrica", "vehiculo hibrido", "vehiculos hibridos",
    "electrofahrzeug", "elektrofahrzeuge", "elektroauto", "hybridfahrzeug",
    "vehicule electrique", "vehicules electriques", "voiture electrique",
    "vehicule hybride", "kenderaan elektrik", "formula sae eletrico",
    "formula student eletrico", "baja sae eletrico", "veiculo formula sae eletrico",
}
_ELECTRIC_ADJECTIVES = {
    "electric", "electrical", "eletrico", "eletrica", "eletricos", "eletricas",
    "hybrid", "hibrido", "hibrida", "hibridos", "hibridas", "electrified",
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


def _technical_tokens(value: str) -> set[str]:
    return _tokens(value) - _CONTEXT_TOKENS - _GENERIC_TECHNICAL_TOKENS


def _coverage(needles: set[str], haystack: str) -> float:
    if not needles:
        return 0.0
    present = _tokens(haystack)
    return len(needles & present) / len(needles)


def _technical_coverage(needles: set[str], haystack: str) -> float:
    if not needles:
        return 0.0
    present = _tokens(haystack)
    matched = 0
    for needle in needles:
        if needle in present or bool(_TECHNICAL_ALIASES.get(needle, set()) & present):
            matched += 1
    return matched / len(needles)


def _contains_term(haystack: str, term: str) -> bool:
    needle = normalize_title(term)
    return bool(needle) and f" {needle} " in f" {haystack} "


def electric_vehicle_signal(paper: Paper) -> float:
    """Detect EV/hybrid/fuel-cell scope, including reversed adjective order."""
    title_topics = normalize_title(
        " ".join([paper.title, paper.venue or "", *paper.topics])
    )
    if any(_contains_term(title_topics, term) for term in _ELECTRIC_VEHICLE_TERMS):
        return 1.0
    title_tokens = set(title_topics.split())
    has_electric_adjective = bool(title_tokens & _ELECTRIC_ADJECTIVES)
    has_vehicle_context = any(
        _contains_term(title_topics, term) for term in _STRONG_CONTEXT_TERMS
    )
    if has_electric_adjective and has_vehicle_context:
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
    return electric_vehicle_signal(paper) >= 0.75


def application_context_signal(paper: Paper) -> float:
    haystack = normalize_title(
        " ".join([paper.title, paper.abstract or "", paper.venue or "", *paper.topics])
    )
    direct = sum(1 for term in _DIRECT_COMPETITION_TERMS if _contains_term(haystack, term))
    if direct:
        return 1.0
    title_topics = normalize_title(" ".join([paper.title, *paper.topics]))
    if _contains_term(title_topics, "baja") and any(
        _contains_term(title_topics, term)
        for term in ("veiculo", "veiculos", "vehicle", "vehicles", "carro", "car", "competicao", "competition", "telemetry", "telemetria")
    ):
        return 0.95
    matches = sum(
        1 for term in _STRONG_CONTEXT_TERMS if _contains_term(haystack, term)
    )
    if matches:
        return min(0.90, 0.80 + 0.05 * (matches - 1))
    if any(_contains_term(haystack, term) for term in _MOTORSPORT_TERMS):
        return 0.85
    general = sum(
        1 for term in _GENERAL_VEHICLE_TERMS if _contains_term(haystack, term)
    )
    return min(0.60, 0.30 * general)


def technical_relevance_signal(
    paper: Paper, technical_focus: str, queries: Iterable[str]
) -> float:
    title = normalize_title(paper.title)
    abstract = normalize_title(paper.abstract or "")
    topics = normalize_title(" ".join(paper.topics))
    candidates = [technical_focus, *queries]
    best = 0.0
    for candidate in candidates:
        needles = _technical_tokens(candidate)
        if not needles:
            continue
        score = (
            0.65 * _technical_coverage(needles, title)
            + 0.25 * _technical_coverage(needles, abstract)
            + 0.10 * _technical_coverage(needles, topics)
        )
        normalized_candidate = normalize_title(candidate)
        if normalized_candidate and normalized_candidate in title:
            score += 0.10
        best = max(best, min(1.0, score))
    return best


def _source_relevance(paper: Paper) -> float:
    values: list[float] = []
    for value in paper.source_scores.values():
        try:
            numeric = max(0.0, float(value))
        except (TypeError, ValueError):
            continue
        values.append(numeric if numeric <= 1.0 else numeric / (1.0 + numeric))
    return max(values, default=(0.5 if paper.sources else 0.0))


def _citation_signal(paper: Paper, max_citations: int) -> float:
    if not paper.citation_count or max_citations <= 0:
        return 0.0
    return min(1.0, math.log1p(paper.citation_count) / math.log1p(max_citations))


def _recency_signal(paper: Paper, current_year: int) -> float:
    if paper.year is None:
        return 0.0
    age = max(0, current_year - paper.year)
    return max(0.0, 1.0 - min(age, 40) / 40.0)


def _completeness_signal(paper: Paper) -> float:
    fields = (
        bool(paper.authors),
        paper.year is not None,
        bool(paper.abstract),
        bool(paper.venue or paper.institution),
        bool(paper.document_type),
        bool(paper.doi or paper.oasisbr_id or paper.bdtd_id),
    )
    return sum(fields) / len(fields)


def quality_signals(
    paper: Paper,
    technical_focus: str,
    queries: Iterable[str],
    *,
    max_citations: int = 0,
    current_year: int | None = None,
) -> dict[str, float]:
    current_year = current_year or datetime.now().year
    return {
        "technical_relevance": technical_relevance_signal(
            paper, technical_focus, queries
        ),
        "focus_relevance": technical_relevance_signal(paper, technical_focus, ()),
        "application_context": application_context_signal(paper),
        "source_relevance": _source_relevance(paper),
        "completeness": _completeness_signal(paper),
        "multi_source": min(1.0, max(0, len(set(paper.sources)) - 1) / 2.0),
        "citation_signal": _citation_signal(paper, max_citations),
        "recency_signal": _recency_signal(paper, current_year),
        "long_form": 1.0 if is_long_form_document(paper.document_type) else 0.0,
    }


def filter_relevant_papers(
    papers: Iterable[Paper],
    technical_focus: str,
    queries: Iterable[str],
    *,
    require_context: bool = True,
) -> tuple[list[Paper], dict[str, int]]:
    """Hard-reject wrong-topic and non-Baja records before final ranking."""
    query_list = list(queries)
    kept: list[Paper] = []
    rejected = {"wrong_technical_focus": 0, "missing_baja_context": 0}
    for paper in papers:
        technical = technical_relevance_signal(paper, technical_focus, query_list)
        focus = technical_relevance_signal(paper, technical_focus, ())
        context = application_context_signal(paper)
        if technical < TECHNICAL_RELEVANCE_THRESHOLD or focus < FOCUS_RELEVANCE_THRESHOLD:
            rejected["wrong_technical_focus"] += 1
            continue
        if require_context and context < APPLICATION_CONTEXT_THRESHOLD:
            rejected["missing_baja_context"] += 1
            continue
        kept.append(paper)
    return kept, rejected


def partition_search_papers(
    papers: Iterable[Paper], technical_focus: str, queries: Iterable[str]
) -> tuple[list[Paper], list[Paper], dict[str, int]]:
    """Separate confirmed application matches from explicitly uncertain leads.

    The review tier is not a relaxation of PDF/EV access requirements. It only
    records *why* relevance is uncertain; callers verify every PDF separately.
    """
    query_list = list(queries)
    approved: list[Paper] = []
    review: list[Paper] = []
    counts = {"wrong_technical_focus": 0, "missing_baja_context": 0}
    focus_tokens = _technical_tokens(technical_focus)
    for paper in papers:
        technical = technical_relevance_signal(paper, technical_focus, query_list)
        focus = technical_relevance_signal(paper, technical_focus, ())
        context = application_context_signal(paper)
        technical_ok = (technical >= TECHNICAL_RELEVANCE_THRESHOLD
                        and focus >= FOCUS_RELEVANCE_THRESHOLD)
        context_ok = context >= APPLICATION_CONTEXT_THRESHOLD
        if technical_ok and context_ok:
            approved.append(paper)
            continue
        if not technical_ok:
            counts["wrong_technical_focus"] += 1
            # A Baja telemetry paper is a plausible LoRa lead, but must not
            # be called an actual LoRa result without source evidence.
            haystack = normalize_title(" ".join([paper.title, paper.abstract or "", *paper.topics]))
            lora_neighbor = "lora" in focus_tokens and bool(
                set(haystack.split()) & {"telemetry", "telemetria", "wireless", "radio", "transceiver", "transmitter"}
            )
            if context_ok and (focus >= 0.15 or lora_neighbor):
                paper.metadata["review_reason"] = "technical_focus_unconfirmed"
                review.append(paper)
        else:
            counts["missing_baja_context"] += 1
            # Transfer candidates still need an explicit technical signal in
            # their title/topics. A stray abstract mention must not make a
            # generic IoT or aquaculture work a WhatsApp suggestion.
            title_topics = " ".join([paper.title, *paper.topics])
            if focus_tokens and _technical_coverage(focus_tokens, title_topics) >= 0.5:
                paper.metadata["review_reason"] = "indirect_baja_application"
                review.append(paper)
    return approved, review, counts


def rank_papers(
    papers: Iterable[Paper],
    queries: Iterable[str],
    *,
    technical_focus: str | None = None,
    limit: int | None = None,
    current_year: int | None = None,
    prefer_theses: bool = False,
    require_context: bool = False,
) -> list[Paper]:
    """Rank within a document class; long-form ordering is a separate policy."""
    query_list = [str(query).strip() for query in queries if str(query).strip()]
    focus = (technical_focus or (query_list[0] if query_list else "")).strip()
    current_year = current_year or datetime.now().year
    values = list(papers)
    max_citations = max((paper.citation_count or 0 for paper in values), default=0)
    ranked: list[Paper] = []
    for paper in values:
        components = quality_signals(
            paper,
            focus,
            query_list,
            max_citations=max_citations,
            current_year=current_year,
        )
        score = (
            0.52 * components["technical_relevance"]
            + 0.25 * components["application_context"]
            + 0.08 * components["source_relevance"]
            + 0.05 * components["completeness"]
            + 0.04 * components["multi_source"]
            + 0.04 * components["citation_signal"]
            + 0.02 * components["recency_signal"]
        )
        # Compatibility parameters remain accepted, but neither can weaken the
        # hard gates applied by ``filter_relevant_papers`` in the service.
        components["passes_technical_gate"] = float(
            components["technical_relevance"] >= TECHNICAL_RELEVANCE_THRESHOLD
            and components["focus_relevance"] >= FOCUS_RELEVANCE_THRESHOLD
        )
        components["passes_context_gate"] = float(
            not require_context
            or components["application_context"] >= APPLICATION_CONTEXT_THRESHOLD
        )
        ranked.append(
            replace(
                paper,
                ranking_score=score,
                score_details=components,
                ranking_version=RANKING_VERSION,
            )
        )
    ranked.sort(
        key=lambda paper: (
            -(paper.ranking_score or 0.0),
            -(paper.citation_count or 0),
            normalize_title(paper.title),
            paper.internal_id,
        )
    )
    return ranked[:limit] if limit is not None else ranked


__all__ = [
    "APPLICATION_CONTEXT_THRESHOLD",
    "RANKING_VERSION",
    "TECHNICAL_RELEVANCE_THRESHOLD",
    "application_context_signal",
    "electric_vehicle_signal",
    "filter_relevant_papers",
    "partition_search_papers",
    "is_electric_vehicle_paper",
    "quality_signals",
    "rank_papers",
    "technical_relevance_signal",
]
