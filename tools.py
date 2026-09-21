"""Hermes tool handlers and the BAJA Research orchestration service."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Callable, Iterable, Mapping

try:
    from .clients.base import SourceError, SourceResult, timed_call
    from .clients.crossref import CrossrefClient
    from .clients.link_validator import LinkValidator
    from .clients.openalex import OpenAlexClient
    from .clients.semantic_scholar import SemanticScholarClient
    from .models import Paper, deduplicate_papers, merge_papers, normalize_doi
    from .ranking import is_electric_vehicle_paper, rank_papers
    from .schemas import (
        CITATION_SCHEMA,
        GET_PAPER_SCHEMA,
        RELATED_SCHEMA,
        SEARCH_SCHEMA,
        STATS_SCHEMA,
        validate_identifier_args,
        validate_limit,
        validate_search_args,
        validate_style,
    )
    from .storage import ResearchStorage
except ImportError:  # pragma: no cover - direct module imports
    from clients.base import SourceError, SourceResult, timed_call
    from clients.crossref import CrossrefClient
    from clients.link_validator import LinkValidator
    from clients.openalex import OpenAlexClient
    from clients.semantic_scholar import SemanticScholarClient
    from models import Paper, deduplicate_papers, merge_papers, normalize_doi
    from ranking import is_electric_vehicle_paper, rank_papers
    from schemas import (
        CITATION_SCHEMA,
        GET_PAPER_SCHEMA,
        RELATED_SCHEMA,
        SEARCH_SCHEMA,
        STATS_SCHEMA,
        validate_identifier_args,
        validate_limit,
        validate_search_args,
        validate_style,
    )
    from storage import ResearchStorage


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResearchConfig:
    cache_ttl_hours: float = 24.0
    request_timeout_seconds: float = 15.0
    max_retries: int = 2
    openalex_api_key: str | None = None
    semantic_scholar_api_key: str | None = None
    crossref_mailto: str | None = None
    validate_links: bool = True
    link_timeout_seconds: float = 6.0


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, min(4, int(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _context_setting(ctx: Any, name: str, default: Any) -> Any:
    try:
        value = ctx.get_config(name, default=default)
        return default if value is None else value
    except Exception:
        return default


def _config_from_context(ctx: Any) -> ResearchConfig:
    context_ttl = _context_setting(ctx, "cache_ttl_hours", 24)
    context_timeout = _context_setting(ctx, "request_timeout_seconds", 15)
    try:
        context_ttl = float(context_ttl)
    except (TypeError, ValueError):
        context_ttl = 24.0
    try:
        context_timeout = float(context_timeout)
    except (TypeError, ValueError):
        context_timeout = 15.0
    return ResearchConfig(
        cache_ttl_hours=_env_float(
            "BAJA_RESEARCH_CACHE_TTL_HOURS",
            context_ttl,
        ),
        request_timeout_seconds=_env_float(
            "BAJA_RESEARCH_REQUEST_TIMEOUT_SECONDS",
            context_timeout,
        ),
        max_retries=_env_int("BAJA_RESEARCH_MAX_RETRIES", 2),
        openalex_api_key=os.getenv("OPENALEX_API_KEY") or None,
        semantic_scholar_api_key=os.getenv("SEMANTIC_SCHOLAR_API_KEY") or None,
        crossref_mailto=os.getenv("CROSSREF_MAILTO") or None,
        validate_links=_env_bool("BAJA_RESEARCH_VALIDATE_LINKS", True),
        link_timeout_seconds=_env_float("BAJA_RESEARCH_LINK_TIMEOUT_SECONDS", 6.0),
    )


def _cache_key(queries: list[str], filters: Mapping[str, Any]) -> str:
    return json.dumps(
        {"queries": queries, "filters": dict(filters)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


_BAJA_CONTEXT_MARKERS = (
    "baja", "formula sae", "formula student", "off road", "off-road", "offroad",
    "atv", "automotive", "vehicle", "motorsport", "telemetry", "can bus",
)
_THESIS_QUERY_MARKERS = (
    "thesis", "dissertation", "tcc", "monograph", "monografia",
    "undergraduate thesis", "master thesis", "doctoral thesis",
    "institutional repository", "repository", "repositorio",
)


def _expand_plugin_queries(
    queries: list[str], *, baja_context: bool, prefer_theses: bool
) -> list[str]:
    """Add conservative retrieval guards without replacing LLM expansion.

    Hermes still owns the semantic expansion. These additions protect the
    plugin when a broad request such as ``electronics`` reaches the tool
    without any Baja/vehicle context or a thesis-oriented variant.
    """
    expanded = list(queries)
    joined = " ".join(expanded).casefold()
    base = expanded[0]
    if baja_context and not any(marker in joined for marker in _BAJA_CONTEXT_MARKERS):
        expanded.extend(
            (
                f"Baja SAE {base}",
                f"{base} off-road vehicle Formula SAE",
            )
        )
    joined = " ".join(expanded).casefold()
    if prefer_theses and not any(marker in joined for marker in _THESIS_QUERY_MARKERS):
        expanded.extend(
            (
                f"{base} Baja SAE thesis dissertation",
                f"{base} off-road vehicle undergraduate thesis institutional repository",
            )
        )
    return list(dict.fromkeys(expanded))[:8]


class ResearchService:
    """Coordinate sources, cache, deduplication, ranking and formatting."""

    def __init__(
        self,
        *,
        config: ResearchConfig | None = None,
        storage: ResearchStorage | None = None,
        clients: Mapping[str, Any] | None = None,
        link_validator: LinkValidator | None = None,
    ) -> None:
        self.config = config or ResearchConfig()
        self.storage = storage or ResearchStorage(
            Path("baja_research.sqlite3"), ttl_hours=self.config.cache_ttl_hours
        )
        self.clients: dict[str, Any] = dict(
            clients
            or {
                "openalex": OpenAlexClient(
                    api_key=self.config.openalex_api_key,
                    timeout=self.config.request_timeout_seconds,
                    max_retries=self.config.max_retries,
                ),
                "semantic_scholar": SemanticScholarClient(
                    api_key=self.config.semantic_scholar_api_key,
                    timeout=self.config.request_timeout_seconds,
                    max_retries=self.config.max_retries,
                ),
                "crossref": CrossrefClient(
                    mailto=self.config.crossref_mailto,
                    timeout=self.config.request_timeout_seconds,
                    max_retries=self.config.max_retries,
                ),
            }
        )
        self.link_validator = link_validator or LinkValidator(
            timeout=self.config.link_timeout_seconds
        )
        self.last_status: dict[str, Any] = {}

    @classmethod
    def from_hermes_context(cls, ctx: Any) -> "ResearchService":
        config = _config_from_context(ctx)
        data_dir = Path(ctx.state.data_dir)
        storage = ResearchStorage(
            data_dir / "baja_research.sqlite3", ttl_hours=config.cache_ttl_hours
        )
        return cls(config=config, storage=storage)

    def close(self) -> None:
        for client in self.clients.values():
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    logger.debug("source client close failed", exc_info=True)
        self.link_validator.close()

    def _validate_papers(self, papers: list[Paper]) -> None:
        """Mark only successfully reachable links as public output links."""
        if not self.config.validate_links or not papers:
            return
        urls = [
            url
            for paper in papers
            for url in (paper.url, paper.open_access_url)
            if url
        ]
        checks = self.link_validator.check_many(urls)
        counts: dict[str, int] = {"valid": 0, "invalid": 0, "unknown": 0}
        for paper in papers:
            for field, raw_url in (
                ("url", paper.url),
                ("open_access_url", paper.open_access_url),
            ):
                if not raw_url:
                    paper.link_status[field] = "not_provided"
                    continue
                check = checks.get(raw_url)
                status = check.status if check is not None else "unknown"
                paper.link_status[field] = status
                if status == "valid":
                    verified = check.final_url or raw_url
                    if field == "url":
                        paper.verified_url = verified
                    else:
                        paper.verified_open_access_url = verified
                counts[status] = counts.get(status, 0) + 1
        logger.info(
            "event=link_validation checked=%d valid=%d invalid=%d unknown=%d",
            len(urls),
            counts.get("valid", 0),
            counts.get("invalid", 0),
            counts.get("unknown", 0),
        )

    @staticmethod
    def _prioritize_explicit_context(papers: list[Paper]) -> list[Paper]:
        """Keep generic-domain fallback papers behind explicit Baja context.

        A broad query can still need general automotive literature, but when
        the retrieval set contains explicit Baja/Formula/off-road/ATV or
        motorsport records, those are the useful first-class answer.
        """
        contextual = [
            paper for paper in papers if (paper.score_details.get("context_signal") or 0.0) >= 0.75
        ]
        if not contextual:
            return papers
        contextual_ids = {paper.internal_id for paper in contextual}
        return contextual + [paper for paper in papers if paper.internal_id not in contextual_ids]

    @staticmethod
    def _filter_search_candidates(
        papers: list[Paper],
        *,
        open_access_only: bool,
        exclude_electric_vehicles: bool,
    ) -> tuple[list[Paper], dict[str, int]]:
        """Apply the BAJA default exclusions before ranking.

        ``open_access_url`` is only populated by clients when the source
        provides an open-access signal. Reachability is checked later, after
        ranking, so a dead repository link cannot occupy a final slot.
        """
        kept: list[Paper] = []
        counts = {
            "electric_vehicle": 0,
            "not_open_access": 0,
            "unverified_open_access": 0,
        }
        for paper in papers:
            if exclude_electric_vehicles and is_electric_vehicle_paper(paper):
                counts["electric_vehicle"] += 1
                continue
            if open_access_only and not paper.open_access_url:
                counts["not_open_access"] += 1
                continue
            kept.append(paper)
        return kept, counts

    def _select_final_papers(
        self,
        ranked: list[Paper],
        *,
        limit: int,
        open_access_only: bool,
    ) -> tuple[list[Paper], int]:
        """Select final results, validating OA URLs before recommendation."""
        if not open_access_only:
            final = ranked[:limit]
            self._validate_papers(final)
            return final, 0
        if not self.config.validate_links:
            # Explicit diagnostic mode: source-provided OA evidence is still
            # required, but link verification has been intentionally disabled.
            return ranked[:limit], 0

        # Validate a bounded ranked window so dead repository records can be
        # skipped without issuing a request for every raw source result.
        candidate_window = ranked[: min(len(ranked), max(40, limit * 8))]
        self._validate_papers(candidate_window)
        usable = [
            paper
            for paper in candidate_window
            if paper.link_status.get("open_access_url") == "valid"
        ]
        rejected = len(candidate_window) - len(usable)
        return usable[:limit], rejected

    def _source_info(self, source: str, client: Any) -> dict[str, Any]:
        return {
            "configured": bool(getattr(client, "configured", False)),
            "key_optional": source == "openalex",
        }

    def _parallel(self, operations: list[tuple[str, str | None, Callable[[], list[Paper]]]]) -> list[SourceResult]:
        if not operations:
            return []
        results: list[SourceResult] = []
        workers = min(8, len(operations))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="baja-research") as executor:
            futures = {
                executor.submit(timed_call, source, operation, query=query): (source, query)
                for source, query, operation in operations
            }
            for future in as_completed(futures):
                source, query = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:  # future-level defensive boundary
                    results.append(
                        SourceResult(
                            source=source,
                            query=query,
                            error={
                                "source": source,
                                "code": "unexpected_source_error",
                                "message": f"{type(exc).__name__}: {exc}",
                                "retryable": False,
                            },
                        )
                    )
        return results

    def _aggregate_statuses(self, results: Iterable[SourceResult]) -> dict[str, Any]:
        grouped: dict[str, list[SourceResult]] = {source: [] for source in self.clients}
        for result in results:
            grouped.setdefault(result.source, []).append(result)
        statuses: dict[str, Any] = {}
        for source, client in self.clients.items():
            items = grouped.get(source, [])
            successes = [item for item in items if item.error is None]
            failures = [item for item in items if item.error is not None]
            if not items:
                status = "not_tested"
            elif successes and failures:
                status = "partial"
            elif successes:
                status = "ok"
            else:
                status = "error"
            errors: list[dict[str, Any]] = []
            seen: set[str] = set()
            for item in failures:
                error = item.error or {}
                marker = f"{error.get('code')}:{error.get('message')}"
                if marker not in seen:
                    seen.add(marker)
                    errors.append(error)
            statuses[source] = {
                **self._source_info(source, client),
                "status": status,
                "queries_attempted": len(items),
                "result_count": sum(len(item.papers) for item in successes),
                "latency_ms": round(max((item.latency_ms for item in items), default=0.0), 2),
                "errors": errors,
            }
            logger.info(
                "source=%s event=academic_search status=%s latency_ms=%.2f result_count=%d errors=%d",
                source,
                status,
                statuses[source]["latency_ms"],
                statuses[source]["result_count"],
                len(errors),
            )
        return statuses

    @staticmethod
    def _warnings(statuses: Mapping[str, Any]) -> list[str]:
        warnings: list[str] = []
        for source, status in statuses.items():
            for error in status.get("errors", []):
                message = error.get("message") or "source unavailable"
                warnings.append(f"{source}: {message}")
        return warnings

    def _search_response(
        self,
        *,
        queries: list[str],
        filters: Mapping[str, Any],
        papers: list[Paper],
        total_found: int,
        statuses: Mapping[str, Any],
        cache_hit: bool,
        cache_created_at: str | None = None,
        filter_counts: Mapping[str, int] | None = None,
        filter_warnings: Iterable[str] = (),
    ) -> dict[str, Any]:
        warnings = [*self._warnings(statuses), *filter_warnings]
        response: dict[str, Any] = {
            "ok": bool(papers) or any(value.get("status") == "ok" for value in statuses.values()),
            "queries": queries,
            "filters": dict(filters),
            "cache": {
                "hit": cache_hit,
                "ttl_hours": self.config.cache_ttl_hours,
                "created_at": cache_created_at,
            },
            "total_found": total_found,
            "returned": len(papers),
            "results": [paper.to_dict(compact=True) for paper in papers],
            "sources": dict(statuses),
            "warnings": warnings,
        }
        if filter_counts:
            response["filters_applied"] = {
                key: value for key, value in filter_counts.items() if value
            }
        if not papers and not response["ok"]:
            response["error"] = {
                "code": "all_sources_unavailable",
                "message": "No academic source returned usable results.",
            }
        return response

    def search(
        self,
        *,
        queries: list[str],
        limit: int = 5,
        year_from: int | None = None,
        year_to: int | None = None,
        open_access_only: bool = True,
        prefer_theses: bool = True,
        baja_context: bool = True,
        exclude_electric_vehicles: bool = True,
        original_query: str | None = None,
        refresh_cache: bool = False,
    ) -> dict[str, Any]:
        effective_queries = _expand_plugin_queries(
            queries, baja_context=baja_context, prefer_theses=prefer_theses
        )
        filters = {
            "limit": limit,
            "year_from": year_from,
            "year_to": year_to,
            "open_access_only": open_access_only,
            "prefer_theses": prefer_theses,
            "baja_context": baja_context,
            "exclude_electric_vehicles": exclude_electric_vehicles,
        }
        key = _cache_key(effective_queries, filters)
        if not refresh_cache and self.config.cache_ttl_hours > 0:
            cached = self.storage.get_cached_search(key, ttl_hours=self.config.cache_ttl_hours)
            if cached is not None:
                statuses = cached.get("source_status", {})
                self.last_status = dict(statuses)
                cached_papers = cached["papers"][:limit]
                self._validate_papers(cached_papers)
                if open_access_only:
                    cached_papers = [
                        paper
                        for paper in cached_papers
                        if paper.link_status.get("open_access_url") == "valid"
                    ]
                self.storage.upsert_papers(cached_papers)
                logger.info("event=academic_search cache=hit returned=%d", len(cached_papers))
                return self._search_response(
                    queries=effective_queries,
                    filters=filters,
                    papers=cached_papers,
                    total_found=int(cached.get("total_found", len(cached["papers"]))),
                    statuses=statuses,
                    cache_hit=True,
                    cache_created_at=cached.get("created_at"),
                )

        source_limit = max(8, min(50, limit * 3))
        operations: list[tuple[str, str | None, Callable[[], list[Paper]]]] = []
        for query in effective_queries:
            for source, client in self.clients.items():
                operations.append(
                    (
                        source,
                        query,
                        lambda client=client, query=query: client.search(
                            query,
                            limit=source_limit,
                            year_from=year_from,
                            year_to=year_to,
                            open_access_only=open_access_only,
                        ),
                    )
                )
        source_results = self._parallel(operations)
        statuses = self._aggregate_statuses(source_results)
        self.last_status = statuses
        all_papers = [paper for result in source_results if result.error is None for paper in result.papers]
        filtered_papers, filter_counts = self._filter_search_candidates(
            all_papers,
            open_access_only=open_access_only,
            exclude_electric_vehicles=exclude_electric_vehicles,
        )
        unique = deduplicate_papers(filtered_papers)
        ranked = rank_papers(
            unique,
            effective_queries,
            limit=None,
            prefer_theses=prefer_theses,
            require_context=baja_context,
        )
        stored = self.storage.upsert_papers(ranked)
        ranked = rank_papers(
            stored,
            effective_queries,
            limit=None,
            prefer_theses=prefer_theses,
            require_context=baja_context,
        )
        ranked = self._prioritize_explicit_context(ranked)
        final, rejected_links = self._select_final_papers(
            ranked, limit=limit, open_access_only=open_access_only
        )
        filter_counts["unverified_open_access"] = rejected_links
        final = self.storage.upsert_papers(final)
        filter_warnings: list[str] = []
        if filter_counts["electric_vehicle"]:
            filter_warnings.append(
                f"{filter_counts['electric_vehicle']} resultado(s) sobre veículos elétricos/híbridos foram excluídos."
            )
        if open_access_only and filter_counts["unverified_open_access"]:
            filter_warnings.append(
                f"{filter_counts['unverified_open_access']} resultado(s) open access foram descartados porque o link de acesso não pôde ser verificado."
            )
        if open_access_only and not final:
            filter_warnings.append(
                "Nenhum resultado com acesso aberto e link verificável permaneceu após os filtros."
            )
        self.storage.save_search(
            cache_key=key,
            original_query=original_query,
            expanded_queries=effective_queries,
            filters=filters,
            papers=final,
            source_status=statuses,
            total_found=len(unique),
        )
        logger.info(
            "event=academic_search cache=miss queries=%d unique_results=%d returned=%d",
            len(effective_queries),
            len(unique),
            len(final),
        )
        return self._search_response(
            queries=effective_queries,
            filters=filters,
            papers=final,
            total_found=len(unique),
            statuses=statuses,
            cache_hit=False,
            filter_counts=filter_counts,
            filter_warnings=filter_warnings,
        )

    def _get_model(self, identifier: str) -> tuple[Paper | None, dict[str, Any], list[str]]:
        cached = self.storage.find_paper(identifier)
        if cached is not None:
            self._validate_papers([cached])
            cached = self.storage.upsert_papers([cached])[0]
            return cached, self.last_status or self._not_tested_status(), []
        operations: list[tuple[str, str | None, Callable[[], list[Paper]]]] = []
        for source, client in self.clients.items():
            if source == "crossref" and not normalize_doi(identifier):
                continue
            operations.append(
                (
                    source,
                    None,
                    lambda client=client: [paper] if (paper := client.get(identifier)) else [],
                )
            )
        source_results = self._parallel(operations)
        statuses = self._aggregate_statuses(source_results)
        self.last_status = statuses
        papers = deduplicate_papers(
            [paper for result in source_results if result.error is None for paper in result.papers]
        )
        if not papers:
            return None, statuses, self._warnings(statuses)
        merged = papers[0]
        for paper in papers[1:]:
            merged = merge_papers(merged, paper)
        stored = self.storage.upsert_papers([merged])
        self._validate_papers(stored)
        stored = self.storage.upsert_papers(stored)
        return (stored[0] if stored else merged), statuses, self._warnings(statuses)

    def _not_tested_status(self) -> dict[str, Any]:
        return {
            source: {**self._source_info(source, client), "status": "not_tested", "queries_attempted": 0, "result_count": 0, "latency_ms": 0.0, "errors": []}
            for source, client in self.clients.items()
        }

    def get_paper(self, *, identifier: str) -> dict[str, Any]:
        paper, statuses, warnings = self._get_model(identifier)
        if paper is None:
            return {
                "ok": False,
                "identifier": identifier,
                "paper": None,
                "sources": statuses,
                "warnings": warnings,
                "error": {"code": "paper_not_found", "message": "No consolidated paper was found for that identifier."},
            }
        return {
            "ok": True,
            "identifier": identifier,
            "paper": paper.to_dict(compact=False),
            "sources": statuses,
            "warnings": warnings,
        }

    def find_related_papers(self, *, identifier: str, limit: int = 5) -> dict[str, Any]:
        paper, initial_statuses, initial_warnings = self._get_model(identifier)
        if paper is None:
            return {
                "ok": False,
                "identifier": identifier,
                "results": [],
                "sources": initial_statuses,
                "warnings": initial_warnings,
                "error": {"code": "paper_not_found", "message": "The reference paper was not found."},
            }

        native_ops: list[tuple[str, str | None, Callable[[], list[Paper]]]] = []
        s2 = self.clients.get("semantic_scholar")
        if s2 is not None and paper.semantic_scholar_id:
            native_ops.append(("semantic_scholar", None, lambda: s2.related(paper.semantic_scholar_id, limit=limit * 2)))
        oa = self.clients.get("openalex")
        if oa is not None and paper.openalex_id:
            native_ops.append(("openalex", None, lambda: oa.related(paper.openalex_id, limit=limit * 2)))
        native_results = self._parallel(native_ops)
        statuses = self._aggregate_statuses(native_results) if native_results else self._not_tested_status()
        for source, status in initial_statuses.items():
            if statuses[source]["status"] == "not_tested" and status.get("status") != "not_tested":
                statuses[source] = status
        related = [paper for result in native_results if result.error is None for paper in result.papers]
        related, related_filter_counts = self._filter_search_candidates(
            related,
            open_access_only=True,
            exclude_electric_vehicles=True,
        )

        if len(related) < limit:
            query = paper.title
            if paper.topics:
                query = f"{query} {' '.join(paper.topics[:3])}"
            fallback = self.search(queries=[query], limit=min(20, max(limit * 2, 5)), refresh_cache=False)
            statuses = fallback.get("sources", statuses)
            related.extend(Paper.from_dict(item) for item in fallback.get("results", []))
            related, fallback_filter_counts = self._filter_search_candidates(
                related,
                open_access_only=True,
                exclude_electric_vehicles=True,
            )
            for key, value in fallback_filter_counts.items():
                related_filter_counts[key] = related_filter_counts.get(key, 0) + value
        related = deduplicate_papers(related)
        related = [candidate for candidate in related if not self._same_paper(candidate, paper)]
        related = rank_papers(
            related,
            [paper.title, *paper.topics],
            limit=None,
            prefer_theses=True,
            require_context=True,
        )
        related = self._prioritize_explicit_context(related)
        related, rejected_links = self._select_final_papers(
            related, limit=limit, open_access_only=True
        )
        related_filter_counts["unverified_open_access"] = rejected_links
        stored = self.storage.upsert_papers(related)
        related = rank_papers(
            stored,
            [paper.title, *paper.topics],
            limit=None,
            prefer_theses=True,
            require_context=True,
        )
        related = self._prioritize_explicit_context(related)
        related = related[:limit]
        warnings = self._warnings(statuses)
        if related_filter_counts["electric_vehicle"]:
            warnings.append(
                f"{related_filter_counts['electric_vehicle']} resultado(s) sobre veículos elétricos/híbridos foram excluídos."
            )
        if related_filter_counts["unverified_open_access"]:
            warnings.append(
                f"{related_filter_counts['unverified_open_access']} resultado(s) foram descartados por falta de link open access verificável."
            )
        return {
            "ok": bool(related) or any(status.get("status") == "ok" for status in statuses.values()),
            "identifier": identifier,
            "related_to": paper.to_dict(compact=True),
            "returned": len(related),
            "results": [candidate.to_dict(compact=True) for candidate in related],
            "sources": statuses,
            "warnings": warnings,
            "filters_applied": {
                key: value
                for key, value in related_filter_counts.items()
                if value
            },
        }

    @staticmethod
    def _same_paper(left: Paper, right: Paper) -> bool:
        if left.doi and right.doi and left.doi == right.doi:
            return True
        if left.openalex_id and right.openalex_id and left.openalex_id == right.openalex_id:
            return True
        if left.semantic_scholar_id and right.semantic_scholar_id and left.semantic_scholar_id == right.semantic_scholar_id:
            return True
        return left.internal_id == right.internal_id

    def format_citation(self, *, identifier: str, style: str = "abnt") -> dict[str, Any]:
        paper, statuses, warnings = self._get_model(identifier)
        if paper is None:
            return {
                "ok": False,
                "style": style,
                "sources": statuses,
                "warnings": warnings,
                "error": {"code": "paper_not_found", "message": "Cannot cite a paper that was not found."},
            }
        citation = _format_abnt(paper) if style == "abnt" else _format_bibtex(paper)
        return {
            "ok": True,
            "style": style,
            "citation": citation,
            "paper": paper.to_dict(compact=False),
            "sources": statuses,
            "warnings": warnings,
        }

    def cache_stats(self) -> dict[str, Any]:
        result = self.storage.stats()
        result["sources_configured"] = {
            source: self._source_info(source, client) for source, client in self.clients.items()
        }
        result["api_endpoints"] = {
            "openalex": "https://api.openalex.org",
            "semantic_scholar": "https://api.semanticscholar.org/graph/v1",
            "crossref": "https://api.crossref.org",
        }
        result["last_observed_api_status"] = self.last_status or result.get("last_source_status", {})
        return {"ok": True, **result}


def _author_for_bibtex(name: str) -> str:
    return name.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _bibtex_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _format_abnt(paper: Paper) -> str:
    parts: list[str] = []
    if paper.authors:
        parts.append("; ".join(paper.authors))
    if paper.title:
        parts.append(paper.title)
    if paper.venue:
        parts.append(paper.venue)
    if paper.year is not None:
        parts.append(str(paper.year))
    if paper.doi:
        parts.append(f"DOI: {paper.doi}")
    else:
        public_url = paper.to_dict(compact=True).get("url")
        if public_url:
            parts.append(f"Disponível em: {public_url}")
    return ". ".join(parts) + ("." if parts else "")


def _format_bibtex(paper: Paper) -> str:
    first_author = paper.authors[0].split()[-1].lower() if paper.authors else "paper"
    slug = re.sub(r"[^a-z0-9]+", "", first_author) or "paper"
    key = f"{slug}{paper.year or ''}"
    fields: list[tuple[str, str]] = [("title", paper.title)]
    if paper.authors:
        fields.append(("author", " and ".join(_author_for_bibtex(author) for author in paper.authors)))
    if paper.year is not None:
        fields.append(("year", str(paper.year)))
    if paper.venue:
        fields.append(("journal", paper.venue))
    if paper.doi:
        fields.append(("doi", paper.doi))
    else:
        public_url = paper.to_dict(compact=True).get("url")
        if public_url:
            fields.append(("url", public_url))
    lines = [f"@article{{{key},"]
    lines.extend(f"  {name} = {{{_bibtex_value(value)}}}," for name, value in fields)
    lines.append("}")
    return "\n".join(lines)


def _tool_error(message: str, code: str = "invalid_arguments") -> str:
    return json.dumps({"ok": False, "error": {"code": code, "message": message}}, ensure_ascii=False)


def _safe_handler(function: Callable[[Mapping[str, Any]], dict[str, Any]]) -> Callable:
    def handler(args: Mapping[str, Any], **_: Any) -> str:
        try:
            return json.dumps(function(args), ensure_ascii=False, separators=(",", ":"))
        except ValueError as exc:
            return _tool_error(str(exc))
        except Exception as exc:
            logger.exception("BAJA Research tool failed")
            return _tool_error(f"{type(exc).__name__}: {exc}", "tool_error")

    return handler


def build_tool_handlers(service: ResearchService) -> list[tuple[str, dict[str, Any], Callable, str]]:
    """Return tool registrations in the public Hermes ``ctx.register_tool`` shape."""

    def search(args: Mapping[str, Any]) -> dict[str, Any]:
        return service.search(**validate_search_args(args))

    def get_paper(args: Mapping[str, Any]) -> dict[str, Any]:
        return service.get_paper(identifier=validate_identifier_args(args))

    def related(args: Mapping[str, Any]) -> dict[str, Any]:
        return service.find_related_papers(
            identifier=validate_identifier_args(args), limit=validate_limit(args)
        )

    def citation(args: Mapping[str, Any]) -> dict[str, Any]:
        return service.format_citation(
            identifier=validate_identifier_args(args), style=validate_style(args)
        )

    def stats(args: Mapping[str, Any]) -> dict[str, Any]:
        return service.cache_stats()

    return [
        ("search_academic_papers", SEARCH_SCHEMA, _safe_handler(search), SEARCH_SCHEMA["description"]),
        ("get_paper", GET_PAPER_SCHEMA, _safe_handler(get_paper), GET_PAPER_SCHEMA["description"]),
        ("find_related_papers", RELATED_SCHEMA, _safe_handler(related), RELATED_SCHEMA["description"]),
        ("format_citation", CITATION_SCHEMA, _safe_handler(citation), CITATION_SCHEMA["description"]),
        ("research_cache_stats", STATS_SCHEMA, _safe_handler(stats), STATS_SCHEMA["description"]),
    ]


__all__ = ["ResearchConfig", "ResearchService", "build_tool_handlers"]
