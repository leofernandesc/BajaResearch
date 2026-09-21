"""Hermes tool handlers and the BAJA Research orchestration service."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import time
from typing import Any, Callable, Iterable, Mapping

try:
    from .clients.base import SourceError, SourceResult, timed_call
    from .clients.bdtd import BdtdClient
    from .clients.crossref import CrossrefClient
    from .clients.link_validator import LinkValidator
    from .clients.oasisbr import OasisbrClient
    from .clients.openalex import OpenAlexClient
    from .clients.repositories import RepositoryResolver
    from .clients.semantic_scholar import SemanticScholarClient
    from .clients.unpaywall import UnpaywallClient
    from .citations import format_abnt, format_bibtex
    from .config import ResearchConfig, config_from_context
    from .models import (
        Paper,
        deduplicate_papers,
        is_long_form_document,
        merge_papers,
        normalize_doi,
    )
    from .querying import expand_plugin_queries
    from .routing import SearchRouter
    from .ranking import (
        application_context_signal,
        filter_relevant_papers,
        is_electric_vehicle_paper,
        rank_papers,
        technical_relevance_signal,
    )
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
    from clients.bdtd import BdtdClient
    from clients.crossref import CrossrefClient
    from clients.link_validator import LinkValidator
    from clients.oasisbr import OasisbrClient
    from clients.openalex import OpenAlexClient
    from clients.repositories import RepositoryResolver
    from clients.semantic_scholar import SemanticScholarClient
    from clients.unpaywall import UnpaywallClient
    from citations import format_abnt, format_bibtex
    from config import ResearchConfig, config_from_context
    from models import Paper, deduplicate_papers, is_long_form_document, merge_papers, normalize_doi
    from querying import expand_plugin_queries
    from routing import SearchRouter
    from ranking import application_context_signal, filter_relevant_papers, is_electric_vehicle_paper, rank_papers, technical_relevance_signal
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


def _cache_key(queries: list[str], filters: Mapping[str, Any]) -> str:
    return json.dumps(
        {"queries": queries, "filters": dict(filters)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class ResearchService:
    """Coordinate sources, cache, deduplication, ranking and formatting."""

    def __init__(
        self,
        *,
        config: ResearchConfig | None = None,
        storage: ResearchStorage | None = None,
        clients: Mapping[str, Any] | None = None,
        link_validator: LinkValidator | None = None,
        repository_resolver: RepositoryResolver | None = None,
        unpaywall_client: UnpaywallClient | None = None,
        router: SearchRouter | None = None,
    ) -> None:
        self.config = config or ResearchConfig()
        self.storage = storage or ResearchStorage(
            Path("baja_research.sqlite3"), ttl_hours=self.config.cache_ttl_hours
        )
        self.clients: dict[str, Any] = dict(
            clients
            or {
                "oasisbr": OasisbrClient(
                    timeout=self.config.request_timeout_seconds,
                    max_retries=self.config.max_retries,
                ),
                "bdtd": BdtdClient(
                    timeout=self.config.request_timeout_seconds,
                    max_retries=self.config.max_retries,
                ),
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
            timeout=self.config.access_timeout_seconds,
            storage=self.storage,
            valid_ttl_hours=self.config.access_valid_ttl_hours,
            invalid_ttl_hours=self.config.access_invalid_ttl_hours,
            temporary_ttl_hours=self.config.access_temporary_ttl_hours,
        )
        self.repository_resolver = repository_resolver or RepositoryResolver(
            self.link_validator,
            timeout=self.config.access_timeout_seconds,
        )
        self.unpaywall_client = unpaywall_client or UnpaywallClient(
            email=self.config.unpaywall_email,
            timeout=self.config.request_timeout_seconds,
            max_retries=min(1, self.config.max_retries),
        )
        self.router = router or SearchRouter(
            clients=self.clients,
            storage=self.storage,
            cache_ttl_hours=self.config.source_cache_ttl_hours,
            global_timeout_seconds=max(5.0, self.config.global_timeout_seconds * 0.65),
            circuit_breaker_seconds=self.config.circuit_breaker_seconds,
        )
        self.last_status: dict[str, Any] = {}

    @classmethod
    def from_hermes_context(cls, ctx: Any) -> "ResearchService":
        config = config_from_context(ctx)
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
        self.repository_resolver.close()
        self.unpaywall_client.close()

    def _validate_papers(self, papers: list[Paper]) -> None:
        """Attach a public URL only after anonymous PDF-byte verification."""
        if not papers:
            return
        urls = [
            url
            for paper in papers
            for url in paper.candidate_full_text_urls()
            if url
        ]
        checks = self.link_validator.check_many(urls)
        counts: dict[str, int] = {
            "verified_pdf": 0,
            "invalid": 0,
            "temporary_error": 0,
            "blocked": 0,
        }
        for paper in papers:
            attempts = []
            verified = None
            for raw_url in paper.candidate_full_text_urls():
                check = checks.get(raw_url)
                if check is None:
                    continue
                attempts.append(check.to_dict() if hasattr(check, "to_dict") else {
                    "status": check.status,
                    "url": raw_url,
                    "final_url": getattr(check, "final_url", None),
                })
                counts[check.status] = counts.get(check.status, 0) + 1
                if check.status == "verified_pdf":
                    verified = check
                    break
            if verified is not None:
                paper.access_status = "verified_pdf"
                paper.full_text_url = verified.final_url or verified.url
                paper.access_verified_at = getattr(verified, "checked_at", None) or datetime.now(
                    timezone.utc
                ).isoformat()
                paper.access_evidence = dict(getattr(verified, "evidence", {}) or {})
                paper.access_evidence["attempts"] = attempts
                # Legacy fields remain readable by older cached records.
                paper.link_status["open_access_url"] = "valid"
                paper.verified_open_access_url = paper.full_text_url
            else:
                statuses = [attempt.get("status") for attempt in attempts]
                paper.access_status = (
                    "temporary_error"
                    if "temporary_error" in statuses
                    else "blocked"
                    if "blocked" in statuses
                    else "invalid"
                    if attempts
                    else "not_provided"
                )
                paper.full_text_url = None
                paper.access_evidence = {"attempts": attempts}
                paper.link_status["open_access_url"] = paper.access_status
        logger.info(
            "event=pdf_access_verification checked=%d verified_pdf=%d invalid=%d temporary=%d blocked=%d",
            len(urls),
            counts.get("verified_pdf", 0),
            counts.get("invalid", 0),
            counts.get("temporary_error", 0),
            counts.get("blocked", 0),
        )

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
            has_access_path = bool(paper.candidate_full_text_urls()) or bool(
                paper.landing_url and set(paper.sources) & {"oasisbr", "bdtd"}
            )
            if open_access_only and not has_access_path:
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
        prefer_long_form: bool,
        deadline: float | None = None,
    ) -> tuple[list[Paper], int]:
        """Select final results, validating OA URLs before recommendation."""
        long_form = [paper for paper in ranked if is_long_form_document(paper.document_type)]
        articles = [paper for paper in ranked if not is_long_form_document(paper.document_type)]
        ordered = [*long_form, *articles] if prefer_long_form else [*articles, *long_form]
        if not open_access_only:
            final = ordered[:limit]
            self._validate_papers(final)
            return final, 0
        # Work in small ordered batches and stop as soon as the final count is
        # filled. This bounds network work for WhatsApp while preserving the
        # long-form-first policy.
        candidate_window = ordered[: min(len(ordered), max(12, limit * 4))]
        usable: list[Paper] = []
        attempted = 0
        for offset in range(0, len(candidate_window), 4):
            if deadline is not None and time.monotonic() >= deadline:
                break
            batch = candidate_window[offset : offset + 4]
            attempted += len(batch)
            self._validate_papers(batch)
            for paper in batch:
                if paper.access_status == "verified_pdf" and paper.full_text_url:
                    usable.append(paper)
            if len(usable) >= limit:
                break

            for paper in batch:
                if paper.access_status == "verified_pdf":
                    continue
                if deadline is not None and time.monotonic() >= deadline:
                    break
                try:
                    self.repository_resolver.resolve(paper)
                except Exception:
                    logger.info(
                        "event=repository_resolution status=error paper_id=%s",
                        paper.internal_id,
                        exc_info=True,
                    )
                if paper.doi and self.unpaywall_client.configured:
                    try:
                        resolution = self.unpaywall_client.resolve(paper.doi)
                        existing = list(paper.metadata.get("full_text_candidates") or [])
                        paper.metadata["full_text_candidates"] = list(
                            dict.fromkeys([*existing, *resolution.get("candidates", [])])
                        )
                        paper.provenance["unpaywall"] = {
                            "doi": paper.doi,
                            "is_oa": resolution.get("is_oa"),
                            "oa_status": resolution.get("oa_status"),
                        }
                    except SourceError:
                        logger.info(
                            "event=unpaywall_resolution status=error paper_id=%s",
                            paper.internal_id,
                        )
                self._validate_papers([paper])
                if paper.access_status == "verified_pdf" and paper.full_text_url:
                    usable.append(paper)
                    if len(usable) >= limit:
                        break
            if len(usable) >= limit:
                break
        usable_ids = {paper.internal_id for paper in usable}
        rejected = sum(
            1
            for paper in candidate_window[:attempted]
            if paper.internal_id not in usable_ids
        )
        return usable[:limit], rejected

    def _source_info(self, source: str, client: Any) -> dict[str, Any]:
        return {
            "configured": bool(getattr(client, "configured", False)),
            "key_optional": source in {"openalex", "oasisbr", "bdtd"},
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
                "cache_hits": sum(1 for item in successes if item.cache_hit),
                "skipped_calls": sum(1 for item in items if item.skipped),
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
        technical_focus: str | None = None,
        original_query: str | None = None,
        refresh_cache: bool = False,
    ) -> dict[str, Any]:
        search_deadline = time.monotonic() + self.config.global_timeout_seconds
        effective_queries = expand_plugin_queries(
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
            "technical_focus": technical_focus or queries[0],
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
                        if paper.access_status == "verified_pdf" and paper.full_text_url
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

        source_results = self.router.search(
            queries=effective_queries,
            limit=limit,
            year_from=year_from,
            year_to=year_to,
            prefer_long_form=prefer_theses,
        )
        all_papers = [paper for result in source_results if result.error is None for paper in result.papers]
        filtered_papers, filter_counts = self._filter_search_candidates(
            all_papers,
            open_access_only=open_access_only,
            exclude_electric_vehicles=exclude_electric_vehicles,
        )
        unique = deduplicate_papers(filtered_papers)
        enrichment_results = self.router.enrich(unique, limit=min(3, limit))
        if enrichment_results:
            source_results.extend(enrichment_results)
            unique = deduplicate_papers(
                [
                    *unique,
                    *[
                        paper
                        for result in enrichment_results
                        if result.error is None
                        for paper in result.papers
                    ],
                ]
            )
        focus = (technical_focus or queries[0]).strip()
        repository_candidates = [
            paper
            for paper in unique
            if set(paper.sources) & {"oasisbr", "bdtd"}
            and application_context_signal(paper) >= 0.70
            and (
                technical_relevance_signal(paper, focus, effective_queries) >= 0.30
                or "bdtd" in paper.sources
            )
        ][: max(limit * 2, 6)]
        for paper in repository_candidates:
            if time.monotonic() >= search_deadline:
                break
            try:
                self.repository_resolver.resolve(paper)
            except Exception:
                logger.info(
                    "event=repository_metadata_enrichment status=error paper_id=%s",
                    paper.internal_id,
                    exc_info=True,
                )
        unique, relevance_counts = filter_relevant_papers(
            unique,
            focus,
            effective_queries,
            require_context=baja_context,
        )
        filter_counts.update(relevance_counts)
        statuses = self._aggregate_statuses(source_results)
        self.last_status = statuses
        ranked = rank_papers(
            unique,
            effective_queries,
            technical_focus=focus,
            limit=None,
            prefer_theses=prefer_theses,
            require_context=baja_context,
        )
        stored = self.storage.upsert_papers(ranked)
        ranked = rank_papers(
            stored,
            effective_queries,
            technical_focus=focus,
            limit=None,
            prefer_theses=prefer_theses,
            require_context=baja_context,
        )
        final, rejected_links = self._select_final_papers(
            ranked,
            limit=limit,
            open_access_only=open_access_only,
            prefer_long_form=prefer_theses,
            deadline=search_deadline,
        )
        filter_counts["unverified_open_access"] = rejected_links
        final = self.storage.upsert_papers(final)
        filter_warnings: list[str] = []
        if filter_counts["electric_vehicle"]:
            filter_warnings.append(
                f"{filter_counts['electric_vehicle']} resultado(s) sobre veículos elétricos/híbridos foram excluídos."
            )
        if filter_counts.get("wrong_technical_focus"):
            filter_warnings.append(
                f"{filter_counts['wrong_technical_focus']} resultado(s) foram excluídos por não tratar do foco técnico solicitado."
            )
        if filter_counts.get("missing_baja_context"):
            filter_warnings.append(
                f"{filter_counts['missing_baja_context']} resultado(s) foram excluídos por não ter contexto Baja/Formula/off-road suficiente."
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
        related, relevance_counts = filter_relevant_papers(
            related,
            paper.title,
            [paper.title, *paper.topics],
            require_context=True,
        )
        for key, value in relevance_counts.items():
            related_filter_counts[key] = related_filter_counts.get(key, 0) + value
        related = rank_papers(
            related,
            [paper.title, *paper.topics],
            technical_focus=paper.title,
            limit=None,
            prefer_theses=True,
            require_context=True,
        )
        related, rejected_links = self._select_final_papers(
            related,
            limit=limit,
            open_access_only=True,
            prefer_long_form=True,
        )
        related_filter_counts["unverified_open_access"] = rejected_links
        stored = self.storage.upsert_papers(related)
        related = rank_papers(
            stored,
            [paper.title, *paper.topics],
            technical_focus=paper.title,
            limit=None,
            prefer_theses=True,
            require_context=True,
        )
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
        citation = format_abnt(paper) if style == "abnt" else format_bibtex(paper)
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
        result["resolvers_configured"] = {
            "repository": True,
            "unpaywall": self.unpaywall_client.configured,
        }
        result["api_endpoints"] = {
            "oasisbr": "https://oasisbr.ibict.br/vufind/api/v1",
            "bdtd": "https://bdtd.ibict.br/vufind/api/v1",
            "openalex": "https://api.openalex.org",
            "semantic_scholar": "https://api.semanticscholar.org/graph/v1",
            "crossref": "https://api.crossref.org",
        }
        result["last_observed_api_status"] = self.last_status or result.get("last_source_status", {})
        return {"ok": True, **result}


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
