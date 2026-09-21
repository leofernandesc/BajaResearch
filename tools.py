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
    from .clients.openalex import OpenAlexClient
    from .clients.semantic_scholar import SemanticScholarClient
    from .models import Paper, deduplicate_papers, merge_papers, normalize_doi
    from .ranking import rank_papers
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
    from clients.openalex import OpenAlexClient
    from clients.semantic_scholar import SemanticScholarClient
    from models import Paper, deduplicate_papers, merge_papers, normalize_doi
    from ranking import rank_papers
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
    )


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
    ) -> dict[str, Any]:
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
            "warnings": self._warnings(statuses),
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
        open_access_only: bool = False,
        original_query: str | None = None,
        refresh_cache: bool = False,
    ) -> dict[str, Any]:
        filters = {
            "limit": limit,
            "year_from": year_from,
            "year_to": year_to,
            "open_access_only": open_access_only,
        }
        key = _cache_key(queries, filters)
        if not refresh_cache and self.config.cache_ttl_hours > 0:
            cached = self.storage.get_cached_search(key, ttl_hours=self.config.cache_ttl_hours)
            if cached is not None:
                statuses = cached.get("source_status", {})
                self.last_status = dict(statuses)
                logger.info("event=academic_search cache=hit returned=%d", len(cached["papers"]))
                return self._search_response(
                    queries=queries,
                    filters=filters,
                    papers=cached["papers"][:limit],
                    total_found=int(cached.get("total_found", len(cached["papers"]))),
                    statuses=statuses,
                    cache_hit=True,
                    cache_created_at=cached.get("created_at"),
                )

        source_limit = max(8, min(50, limit * 3))
        operations: list[tuple[str, str | None, Callable[[], list[Paper]]]] = []
        for query in queries:
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
        unique = deduplicate_papers(all_papers)
        ranked = rank_papers(unique, queries, limit=None)
        stored = self.storage.upsert_papers(ranked)
        ranked = rank_papers(stored, queries, limit=None)
        final = ranked[:limit]
        self.storage.upsert_papers(final)
        self.storage.save_search(
            cache_key=key,
            original_query=original_query,
            expanded_queries=queries,
            filters=filters,
            papers=final,
            source_status=statuses,
            total_found=len(unique),
        )
        logger.info(
            "event=academic_search cache=miss queries=%d unique_results=%d returned=%d",
            len(queries),
            len(unique),
            len(final),
        )
        return self._search_response(
            queries=queries,
            filters=filters,
            papers=final,
            total_found=len(unique),
            statuses=statuses,
            cache_hit=False,
        )

    def _get_model(self, identifier: str) -> tuple[Paper | None, dict[str, Any], list[str]]:
        cached = self.storage.find_paper(identifier)
        if cached is not None:
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

        if len(related) < limit:
            query = paper.title
            if paper.topics:
                query = f"{query} {' '.join(paper.topics[:3])}"
            fallback = self.search(queries=[query], limit=min(20, max(limit * 2, 5)), refresh_cache=False)
            statuses = fallback.get("sources", statuses)
            related.extend(Paper.from_dict(item) for item in fallback.get("results", []))
        related = deduplicate_papers(related)
        related = [candidate for candidate in related if not self._same_paper(candidate, paper)]
        related = rank_papers(related, [paper.title, *paper.topics], limit=limit)
        stored = self.storage.upsert_papers(related)
        related = rank_papers(stored, [paper.title, *paper.topics], limit=limit)
        warnings = self._warnings(statuses)
        return {
            "ok": bool(related) or any(status.get("status") == "ok" for status in statuses.values()),
            "identifier": identifier,
            "related_to": paper.to_dict(compact=True),
            "returned": len(related),
            "results": [candidate.to_dict(compact=True) for candidate in related],
            "sources": statuses,
            "warnings": warnings,
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
    elif paper.url:
        parts.append(f"Disponível em: {paper.url}")
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
    elif paper.url:
        fields.append(("url", paper.url))
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
