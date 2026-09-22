"""Bounded source routing, per-query cache and rate-limit circuit breakers."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass
import hashlib
import json
import logging
import threading
import time
from typing import Any, Iterable, Mapping

try:
    from .clients.base import SourceResult, timed_call
    from .models import Paper, is_long_form_document, normalize_title
except ImportError:  # pragma: no cover
    from clients.base import SourceResult, timed_call
    from models import Paper, is_long_form_document, normalize_title


logger = logging.getLogger(__name__)
ROUTER_VERSION = "3"

_PORTUGUESE_TOPIC_WORDS = {
    "suspensao", "chassi", "eletronica", "telemetria", "freios", "freio",
    "direcao", "transmissao", "ergonomia", "manufatura", "soldagem",
    "aquisicao", "amortecedor", "potencia", "estrutura",
}
_ENGLISH_TOPIC_WORDS = {
    "suspension", "chassis", "electronics", "telemetry", "brakes",
    "brake", "steering", "transmission", "ergonomics", "manufacturing",
    "welding", "acquisition", "powertrain", "structure",
}
_RETRIEVAL_NOISE = {
    "pdf", "repository", "repositorio", "institutional", "thesis", "tcc",
    "undergraduate", "monograph", "monografia", "dissertation",
}


@dataclass
class _Circuit:
    failures: int = 0
    open_until: float = 0.0


class SearchRouter:
    """Run useful source calls only, with one in-flight request per source."""

    def __init__(
        self,
        *,
        clients: Mapping[str, Any],
        storage: Any,
        cache_ttl_hours: float = 24.0,
        global_timeout_seconds: float = 25.0,
        circuit_breaker_seconds: float = 60.0,
    ) -> None:
        self.clients = dict(clients)
        self.storage = storage
        self.cache_ttl_hours = max(0.0, float(cache_ttl_hours))
        self.global_timeout_seconds = max(1.0, float(global_timeout_seconds))
        self.circuit_breaker_seconds = max(1.0, float(circuit_breaker_seconds))
        self._circuits: dict[str, _Circuit] = {}
        self._locks: dict[str, threading.Lock] = {
            source: threading.Lock() for source in self.clients
        }

    @staticmethod
    def _cache_key(source: str, query: str, filters: Mapping[str, Any]) -> str:
        payload = json.dumps(
            {
                "version": ROUTER_VERSION,
                "source": source,
                "query": query,
                "filters": dict(filters),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _circuit_error(self, source: str) -> dict[str, Any] | None:
        circuit = self._circuits.setdefault(source, _Circuit())
        remaining = circuit.open_until - time.monotonic()
        if remaining <= 0:
            return None
        return {
            "source": source,
            "code": "circuit_open",
            "message": "source temporarily skipped after rate limit or repeated failure",
            "retryable": True,
            "retry_after_seconds": round(remaining, 2),
        }

    def _observe(self, result: SourceResult) -> None:
        circuit = self._circuits.setdefault(result.source, _Circuit())
        if result.error is None:
            circuit.failures = 0
            circuit.open_until = 0.0
            return
        code = result.error.get("code")
        retryable = bool(result.error.get("retryable"))
        if code == "rate_limited":
            circuit.failures += 1
            retry_after = float(result.error.get("retry_after_seconds") or 0.0)
            circuit.open_until = time.monotonic() + max(
                self.circuit_breaker_seconds, retry_after
            )
        elif retryable:
            circuit.failures += 1
            if circuit.failures >= 2:
                circuit.open_until = time.monotonic() + self.circuit_breaker_seconds
        else:
            circuit.failures = 0

    def _run_query(
        self,
        source: str,
        query: str,
        *,
        filters: Mapping[str, Any],
        deadline: float,
    ) -> SourceResult:
        if time.monotonic() >= deadline:
            return SourceResult(
                source=source,
                query=query,
                error={
                    "source": source,
                    "code": "request_budget_exhausted",
                    "message": "source request budget was exhausted",
                    "retryable": True,
                },
                skipped=True,
            )
        circuit_error = self._circuit_error(source)
        if circuit_error is not None:
            return SourceResult(
                source=source, query=query, error=circuit_error, skipped=True
            )
        key = self._cache_key(source, query, filters)
        cached = self.storage.get_source_query(
            key, ttl_hours=self.cache_ttl_hours
        )
        if cached is not None:
            return SourceResult(
                source=source,
                query=query,
                papers=cached["papers"],
                cache_hit=True,
            )
        client = self.clients[source]
        with self._locks[source]:
            result = timed_call(
                source,
                lambda: client.search(
                    query,
                    limit=filters["source_limit"],
                    year_from=filters.get("year_from"),
                    year_to=filters.get("year_to"),
                    open_access_only=True,
                ),
                query=query,
            )
        self._observe(result)
        if result.error is None:
            self.storage.save_source_query(
                cache_key=key,
                source=source,
                query=query,
                filters=filters,
                papers=result.papers,
                status={"status": "ok", "latency_ms": result.latency_ms},
            )
        return result

    def _run_source(
        self,
        source: str,
        queries: Iterable[str],
        *,
        filters: Mapping[str, Any],
        deadline: float,
    ) -> list[SourceResult]:
        return [
            self._run_query(source, query, filters=filters, deadline=deadline)
            for query in queries
            if time.monotonic() < deadline
        ]

    def _stage(
        self,
        sources: Iterable[str],
        queries: list[str],
        *,
        filters: Mapping[str, Any],
        deadline: float,
    ) -> list[SourceResult]:
        selected = [source for source in sources if source in self.clients]
        if not selected or not queries or time.monotonic() >= deadline:
            return []
        executor = ThreadPoolExecutor(
            max_workers=len(selected), thread_name_prefix="baja-sources"
        )
        futures = {
            executor.submit(
                self._run_source,
                source,
                queries,
                filters=filters,
                deadline=deadline,
            ): source
            for source in selected
        }
        results: list[SourceResult] = []
        try:
            for future, source in list(futures.items()):
                remaining = max(0.0, deadline - time.monotonic())
                try:
                    results.extend(future.result(timeout=remaining))
                except FuturesTimeout:
                    results.append(
                        SourceResult(
                            source=source,
                            error={
                                "source": source,
                                "code": "request_budget_exhausted",
                                "message": "source request budget was exhausted",
                                "retryable": True,
                            },
                            skipped=True,
                        )
                    )
                except Exception as exc:
                    results.append(
                        SourceResult(
                            source=source,
                            error={
                                "source": source,
                                "code": "unexpected_source_error",
                                "message": f"{type(exc).__name__}: {exc}",
                                "retryable": False,
                            },
                        )
                    )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        return results

    @staticmethod
    def _papers(results: Iterable[SourceResult]) -> list[Paper]:
        return [
            paper
            for result in results
            if result.error is None
            for paper in result.papers
        ]

    @staticmethod
    def _prioritized_queries(queries: list[str]) -> list[str]:
        def score(query: str) -> tuple[int, int]:
            normalized = query.casefold().replace("-", " ")
            context = (
                4
                if "baja sae" in normalized or "mini baja" in normalized
                else 3
                if "formula sae" in normalized or "formula student" in normalized
                else 2
                if "off road" in normalized or "all terrain" in normalized
                else 1
                if "vehicle" in normalized or "atv" in normalized
                else 0
            )
            # Repository/thesis tokens are retrieval hints, not technical
            # content. Prefer the shortest strongly contextual query so VuFind
            # does not require every expansion term at once.
            return context, -len(query)

        return sorted(dict.fromkeys(queries), key=score, reverse=True)

    @staticmethod
    def _preferred_query(queries: list[str], *, repository: bool) -> str:
        """Select a concise language-appropriate query for one API request."""
        def priority(query: str) -> tuple[int, int, int, int, int]:
            words = set(normalize_title(query).split())
            desired = _PORTUGUESE_TOPIC_WORDS if repository else _ENGLISH_TOPIC_WORDS
            other = _ENGLISH_TOPIC_WORDS if repository else _PORTUGUESE_TOPIC_WORDS
            contextual = int("baja" in words and "sae" in words)
            language = 2 * int(bool(words & desired)) - int(bool(words & other))
            noise = len(words & _RETRIEVAL_NOISE) + int('"' in query)
            return (contextual, language, -noise, -len(words), -len(query))

        return max(queries, key=priority)

    def search(
        self,
        *,
        queries: list[str],
        limit: int,
        year_from: int | None,
        year_to: int | None,
        prefer_long_form: bool,
        document_type: str = "any",
    ) -> list[SourceResult]:
        started = time.monotonic()
        deadline = started + self.global_timeout_seconds
        source_limit = max(20, min(40, limit * 8))
        filters = {
            "source_limit": source_limit,
            "year_from": year_from,
            "year_to": year_to,
            "open_access_only": True,
        }
        results: list[SourceResult] = []
        prioritized = self._prioritized_queries(queries)
        # One query per keyless endpoint avoids the burst behavior that causes
        # 429/503 responses. Different sources still cover complementary query
        # variants, and each source/query result has its own cache.
        repository_queries = [self._preferred_query(prioritized, repository=True)]
        bdtd_queries = repository_queries
        global_queries = [self._preferred_query(prioritized, repository=False)]

        oasis_deadline = min(deadline, time.monotonic() + 5.0)
        results.extend(
            self._stage(
                ["oasisbr"],
                repository_queries,
                filters=filters,
                deadline=oasis_deadline,
            )
        )
        long_form_count = sum(
            is_long_form_document(paper.document_type)
            for paper in self._papers(results)
        )
        if (
            prefer_long_form
            and document_type != "bachelor_thesis"
            and long_form_count < max(limit * 2, 6)
        ):
            bdtd_deadline = min(deadline, time.monotonic() + 4.0)
            results.extend(
                self._stage(
                    ["bdtd"],
                    bdtd_queries,
                    filters=filters,
                    deadline=bdtd_deadline,
                )
            )

        openalex_deadline = min(deadline, time.monotonic() + 6.0)
        results.extend(
            self._stage(
                ["openalex", "openaire"], global_queries, filters=filters, deadline=openalex_deadline
            )
        )
        usable_count = len(self._papers(results))
        openalex_failed = any(
            result.source == "openalex" and result.error is not None
            for result in results
        )
        if usable_count < max(limit * 3, 12) or openalex_failed:
            results.extend(
                self._stage(
                    ["semantic_scholar"],
                    global_queries,
                    filters=filters,
                    deadline=deadline,
                )
            )
        logger.info(
            "event=source_routing elapsed_ms=%.2f calls=%d results=%d",
            (time.monotonic() - started) * 1000,
            len(results),
            len(self._papers(results)),
        )
        return results

    def enrich(self, papers: list[Paper], *, limit: int = 3) -> list[SourceResult]:
        """Use DOI lookups for metadata only; never create Crossref candidates."""
        deadline = time.monotonic() + min(8.0, self.global_timeout_seconds)
        results: list[SourceResult] = []
        for source in ("semantic_scholar", "crossref"):
            client = self.clients.get(source)
            if client is None:
                continue
            candidates = [
                paper
                for paper in papers
                if paper.doi
                and source not in paper.sources
                and (
                    source == "crossref"
                    and (not paper.authors or paper.year is None or not paper.venue)
                    or source == "semantic_scholar"
                    and paper.citation_count is None
                )
            ][: max(0, limit)]
            for paper in candidates:
                if time.monotonic() >= deadline or self._circuit_error(source):
                    break
                with self._locks[source]:
                    result = timed_call(
                        source,
                        lambda client=client, doi=paper.doi: [resolved]
                        if (resolved := client.get(doi))
                        else [],
                        query=f"doi:{paper.doi}",
                    )
                self._observe(result)
                results.append(result)
        return results


__all__ = ["ROUTER_VERSION", "SearchRouter"]
