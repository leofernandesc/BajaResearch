"""SQLite persistence for normalized papers and search-result cache."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Iterator, Mapping

try:
    from .models import Paper, make_internal_id, merge_papers, normalize_doi, paper_from_storage
except ImportError:  # pragma: no cover - direct test imports
    from models import Paper, make_internal_id, merge_papers, normalize_doi, paper_from_storage


SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    internal_id TEXT PRIMARY KEY,
    doi TEXT UNIQUE,
    openalex_id TEXT,
    semantic_scholar_id TEXT,
    title TEXT NOT NULL,
    abstract TEXT,
    year INTEGER,
    venue TEXT,
    document_type TEXT,
    authors_json TEXT NOT NULL,
    citation_count INTEGER,
    url TEXT,
    open_access_url TEXT,
    verified_url TEXT,
    verified_open_access_url TEXT,
    link_status_json TEXT NOT NULL DEFAULT '{}',
    topics_json TEXT NOT NULL,
    sources_json TEXT NOT NULL,
    source_scores_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    ranking_score REAL,
    score_details_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cache_key TEXT NOT NULL UNIQUE,
    original_query TEXT,
    expanded_queries_json TEXT NOT NULL,
    filters_json TEXT NOT NULL,
    source_status_json TEXT NOT NULL,
    result_count INTEGER NOT NULL DEFAULT 0,
    total_found INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS search_results (
    search_id INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    paper_id TEXT NOT NULL REFERENCES papers(internal_id) ON DELETE CASCADE,
    rank INTEGER NOT NULL,
    score REAL,
    score_details_json TEXT NOT NULL,
    sources_json TEXT NOT NULL,
    PRIMARY KEY (search_id, paper_id)
);

CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi);
CREATE INDEX IF NOT EXISTS idx_papers_openalex ON papers(openalex_id);
CREATE INDEX IF NOT EXISTS idx_papers_s2 ON papers(semantic_scholar_id);
CREATE INDEX IF NOT EXISTS idx_papers_title_year ON papers(title, year);
CREATE INDEX IF NOT EXISTS idx_searches_created_at ON searches(created_at);
CREATE INDEX IF NOT EXISTS idx_search_results_paper ON search_results(paper_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class ResearchStorage:
    """Small thread-safe SQLite repository owned by the plugin."""

    def __init__(self, path: str | Path, *, ttl_hours: float = 24.0) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl_hours = max(0.0, float(ttl_hours))
        self._lock = threading.RLock()
        self.initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._lock, self._connection() as connection:
            connection.executescript(SCHEMA)
            paper_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(papers)").fetchall()
            }
            for name, definition in (
                ("document_type", "TEXT"),
                ("verified_url", "TEXT"),
                ("verified_open_access_url", "TEXT"),
                ("link_status_json", "TEXT NOT NULL DEFAULT '{}'"),
            ):
                if name not in paper_columns:
                    connection.execute(f"ALTER TABLE papers ADD COLUMN {name} {definition}")
            # Keep databases created by early MVP revisions usable after the
            # cache gained a distinction between returned and total results.
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(searches)").fetchall()
            }
            if "total_found" not in columns:
                connection.execute(
                    "ALTER TABLE searches ADD COLUMN total_found INTEGER NOT NULL DEFAULT 0"
                )
                connection.execute(
                    "UPDATE searches SET total_found=result_count WHERE total_found=0"
                )

    @staticmethod
    def _paper_values(paper: Paper, *, created_at: str, updated_at: str) -> tuple[Any, ...]:
        return (
            paper.internal_id,
            paper.doi,
            paper.openalex_id,
            paper.semantic_scholar_id,
            paper.title,
            paper.abstract,
            paper.year,
            paper.venue,
            paper.document_type,
            _json(paper.authors),
            paper.citation_count,
            paper.url,
            paper.open_access_url,
            paper.verified_url,
            paper.verified_open_access_url,
            _json(paper.link_status),
            _json(paper.topics),
            _json(paper.sources),
            _json(paper.source_scores),
            _json(paper.metadata),
            paper.ranking_score,
            _json(paper.score_details),
            created_at,
            updated_at,
        )

    def _find_existing_row(self, connection: sqlite3.Connection, paper: Paper) -> sqlite3.Row | None:
        conditions: list[str] = []
        values: list[Any] = []
        if paper.internal_id:
            conditions.append("internal_id = ?")
            values.append(paper.internal_id)
        if paper.doi:
            conditions.append("doi = ?")
            values.append(paper.doi)
        if paper.openalex_id:
            conditions.append("openalex_id = ?")
            values.append(paper.openalex_id)
        if paper.semantic_scholar_id:
            conditions.append("semantic_scholar_id = ?")
            values.append(paper.semantic_scholar_id)
        if not conditions:
            return None
        return connection.execute(
            f"SELECT * FROM papers WHERE {' OR '.join(conditions)} LIMIT 1", values
        ).fetchone()

    def upsert_papers(self, papers: list[Paper]) -> list[Paper]:
        """Merge incoming records with cache records and return stored papers."""
        stored: list[Paper] = []
        with self._lock, self._connection() as connection:
            for incoming in papers:
                if not incoming.internal_id:
                    incoming.internal_id = make_internal_id(incoming)
                existing_row = self._find_existing_row(connection, incoming)
                now = _now()
                if existing_row is not None:
                    existing = paper_from_storage(existing_row)
                    merged = merge_papers(existing, incoming)
                    # Existing ids are retained so foreign-keyed search results
                    # remain stable even when a later source supplies a DOI.
                    merged.internal_id = existing.internal_id
                    connection.execute(
                        """UPDATE papers SET doi=?, openalex_id=?, semantic_scholar_id=?,
                           title=?, abstract=?, year=?, venue=?, document_type=?, authors_json=?,
                           citation_count=?, url=?, open_access_url=?, verified_url=?,
                           verified_open_access_url=?, link_status_json=?, topics_json=?,
                           sources_json=?, source_scores_json=?, metadata_json=?,
                           ranking_score=?, score_details_json=?, updated_at=?
                           WHERE internal_id=?""",
                        self._paper_values(
                            merged, created_at=existing_row["created_at"], updated_at=now
                        )[1:22] + (now, existing.internal_id),
                    )
                    stored.append(merged)
                    continue

                connection.execute(
                    """INSERT INTO papers (
                       internal_id, doi, openalex_id, semantic_scholar_id, title,
                       abstract, year, venue, document_type, authors_json, citation_count, url,
                       open_access_url, verified_url, verified_open_access_url, link_status_json,
                       topics_json, sources_json, source_scores_json, metadata_json,
                       ranking_score, score_details_json, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    self._paper_values(incoming, created_at=now, updated_at=now),
                )
                stored.append(incoming)
        return stored

    def save_search(
        self,
        *,
        cache_key: str,
        original_query: str | None,
        expanded_queries: list[str],
        filters: Mapping[str, Any],
        papers: list[Paper],
        source_status: Mapping[str, Any],
        total_found: int | None = None,
    ) -> int:
        created_at = _now()
        total_found_value = max(len(papers), int(total_found or 0))
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """INSERT INTO searches (
                   cache_key, original_query, expanded_queries_json, filters_json,
                   source_status_json, result_count, total_found, created_at
                ) VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(cache_key) DO UPDATE SET
                   original_query=excluded.original_query,
                   expanded_queries_json=excluded.expanded_queries_json,
                   filters_json=excluded.filters_json,
                   source_status_json=excluded.source_status_json,
                   result_count=excluded.result_count,
                   total_found=excluded.total_found,
                   created_at=excluded.created_at""",
                (
                    cache_key,
                    original_query,
                    _json(expanded_queries),
                    _json(dict(filters)),
                    _json(dict(source_status)),
                    len(papers),
                    total_found_value,
                    created_at,
                ),
            )
            search_id = cursor.lastrowid
            if not search_id:
                search_id = connection.execute(
                    "SELECT id FROM searches WHERE cache_key=?", (cache_key,)
                ).fetchone()[0]
            connection.execute("DELETE FROM search_results WHERE search_id=?", (search_id,))
            for rank, paper in enumerate(papers, start=1):
                connection.execute(
                    """INSERT INTO search_results (
                       search_id, paper_id, rank, score, score_details_json, sources_json
                    ) VALUES (?,?,?,?,?,?)""",
                    (
                        search_id,
                        paper.internal_id,
                        rank,
                        paper.ranking_score,
                        _json(paper.score_details),
                        _json(paper.sources),
                    ),
                )
        return int(search_id)

    def get_cached_search(self, cache_key: str, *, ttl_hours: float | None = None) -> dict[str, Any] | None:
        ttl = self.ttl_hours if ttl_hours is None else max(0.0, float(ttl_hours))
        threshold = datetime.now(timezone.utc) - timedelta(hours=ttl)
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM searches WHERE cache_key=? ORDER BY id DESC LIMIT 1",
                (cache_key,),
            ).fetchone()
            if row is None:
                return None
            try:
                created_at = datetime.fromisoformat(row["created_at"])
            except (TypeError, ValueError):
                return None
            if created_at < threshold:
                return None
            result_rows = connection.execute(
                """SELECT p.*, sr.rank AS result_rank, sr.score AS result_score,
                          sr.score_details_json AS result_score_details_json
                   FROM search_results sr JOIN papers p ON p.internal_id=sr.paper_id
                   WHERE sr.search_id=? ORDER BY sr.rank""",
                (row["id"],),
            ).fetchall()
            papers: list[Paper] = []
            for result_row in result_rows:
                paper = paper_from_storage(result_row)
                paper.ranking_score = result_row["result_score"]
                try:
                    paper.score_details = json.loads(result_row["result_score_details_json"] or "{}")
                except (TypeError, ValueError):
                    paper.score_details = {}
                papers.append(paper)
            return {
                "search_id": row["id"],
                "created_at": row["created_at"],
                "expanded_queries": json.loads(row["expanded_queries_json"]),
                "filters": json.loads(row["filters_json"]),
                "source_status": json.loads(row["source_status_json"]),
                "total_found": max(int(row["total_found"] or 0), len(papers)),
                "papers": papers,
            }

    def find_paper(self, identifier: str) -> Paper | None:
        raw = str(identifier or "").strip()
        doi = normalize_doi(raw)
        candidates = [raw]
        if raw.startswith("doi:"):
            candidates.append(raw[4:].strip())
        compact = raw.rstrip("/").rsplit("/", 1)[-1]
        candidates.extend([compact, compact.removeprefix("openalex:"), compact.removeprefix("s2:")])
        with self._lock, self._connection() as connection:
            conditions: list[str] = []
            values: list[Any] = []
            if doi:
                conditions.append("doi=?")
                values.append(doi)
            for candidate in dict.fromkeys(candidates):
                conditions.extend(["internal_id=?", "openalex_id=?", "semantic_scholar_id=?"])
                values.extend([candidate, candidate, candidate])
            row = connection.execute(
                f"SELECT * FROM papers WHERE {' OR '.join(conditions)} LIMIT 1", values
            ).fetchone()
            return paper_from_storage(row) if row else None

    def stats(self) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            papers = connection.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
            searches = connection.execute("SELECT COUNT(*) FROM searches").fetchone()[0]
            results = connection.execute("SELECT COUNT(*) FROM search_results").fetchone()[0]
            last = connection.execute(
                "SELECT created_at, source_status_json FROM searches ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return {
                "database_path": str(self.path),
                "papers_cached": int(papers),
                "searches_cached": int(searches),
                "search_results_cached": int(results),
                "last_search_at": last["created_at"] if last else None,
                "last_source_status": json.loads(last["source_status_json"]) if last else {},
            }


__all__ = ["ResearchStorage"]
