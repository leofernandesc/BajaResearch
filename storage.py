"""Versioned SQLite persistence for papers, searches and access evidence."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Iterator, Mapping

try:
    from .models import Paper, make_internal_id, merge_papers, normalize_doi, normalize_title, paper_from_storage
except ImportError:  # pragma: no cover - direct test imports
    from models import Paper, make_internal_id, merge_papers, normalize_doi, normalize_title, paper_from_storage


SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    internal_id TEXT PRIMARY KEY,
    doi TEXT UNIQUE,
    openalex_id TEXT,
    semantic_scholar_id TEXT,
    oasisbr_id TEXT,
    bdtd_id TEXT,
    title TEXT NOT NULL,
    abstract TEXT,
    year INTEGER,
    venue TEXT,
    document_type TEXT,
    institution TEXT,
    language TEXT,
    authors_json TEXT NOT NULL,
    citation_count INTEGER,
    url TEXT,
    open_access_url TEXT,
    landing_url TEXT,
    full_text_url TEXT,
    access_status TEXT NOT NULL DEFAULT 'unknown',
    access_evidence_json TEXT NOT NULL DEFAULT '{}',
    access_verified_at TEXT,
    verified_url TEXT,
    verified_open_access_url TEXT,
    link_status_json TEXT NOT NULL DEFAULT '{}',
    topics_json TEXT NOT NULL,
    sources_json TEXT NOT NULL,
    source_scores_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    ranking_score REAL,
    score_details_json TEXT NOT NULL,
    ranking_version TEXT,
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
    diagnostics_json TEXT NOT NULL DEFAULT '{}',
    algorithm_version TEXT NOT NULL DEFAULT '1',
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

CREATE TABLE IF NOT EXISTS source_query_cache (
    cache_key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    query TEXT NOT NULL,
    filters_json TEXT NOT NULL,
    papers_json TEXT NOT NULL,
    status_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS access_checks (
    url TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    final_url TEXT,
    http_status INTEGER,
    content_type TEXT,
    reason TEXT,
    evidence_json TEXT NOT NULL,
    checked_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi);
CREATE INDEX IF NOT EXISTS idx_papers_openalex ON papers(openalex_id);
CREATE INDEX IF NOT EXISTS idx_papers_s2 ON papers(semantic_scholar_id);
CREATE INDEX IF NOT EXISTS idx_papers_oasisbr ON papers(oasisbr_id);
CREATE INDEX IF NOT EXISTS idx_papers_bdtd ON papers(bdtd_id);
CREATE INDEX IF NOT EXISTS idx_papers_title_year ON papers(title, year);
CREATE INDEX IF NOT EXISTS idx_papers_access ON papers(access_status, document_type);
CREATE INDEX IF NOT EXISTS idx_searches_created_at ON searches(created_at);
CREATE INDEX IF NOT EXISTS idx_search_results_paper ON search_results(paper_id);
CREATE INDEX IF NOT EXISTS idx_source_query_created_at ON source_query_cache(created_at);
CREATE INDEX IF NOT EXISTS idx_access_checked_at ON access_checks(checked_at);
"""


_PAPER_MIGRATIONS = (
    ("abstract", "TEXT"),
    ("venue", "TEXT"),
    ("authors_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("citation_count", "INTEGER"),
    ("url", "TEXT"),
    ("open_access_url", "TEXT"),
    ("topics_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("sources_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("source_scores_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("metadata_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("ranking_score", "REAL"),
    ("score_details_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("created_at", "TEXT NOT NULL DEFAULT ''"),
    ("updated_at", "TEXT NOT NULL DEFAULT ''"),
    ("document_type", "TEXT"),
    ("oasisbr_id", "TEXT"),
    ("bdtd_id", "TEXT"),
    ("institution", "TEXT"),
    ("language", "TEXT"),
    ("landing_url", "TEXT"),
    ("full_text_url", "TEXT"),
    ("access_status", "TEXT NOT NULL DEFAULT 'unknown'"),
    ("access_evidence_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("access_verified_at", "TEXT"),
    ("verified_url", "TEXT"),
    ("verified_open_access_url", "TEXT"),
    ("link_status_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("provenance_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("ranking_version", "TEXT"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class ResearchStorage:
    """Thread-safe plugin-owned SQLite repository with additive migrations."""

    def __init__(self, path: str | Path, *, ttl_hours: float = 24.0) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl_hours = max(0.0, float(ttl_hours))
        self._lock = threading.RLock()
        self.last_backup_path: str | None = None
        self.initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _backup_if_needed(self) -> None:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return
        connection = sqlite3.connect(str(self.path), timeout=30)
        try:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            has_papers = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='papers'"
            ).fetchone()
        finally:
            connection.close()
        if version >= SCHEMA_VERSION or not has_papers:
            return
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = self.path.with_name(f"{self.path.name}.backup-v{version}-{timestamp}")
        # SQLite's backup API includes committed WAL pages; a file copy may not.
        with sqlite3.connect(str(self.path)) as source, sqlite3.connect(str(backup)) as target:
            source.backup(target)
        self.last_backup_path = str(backup)

    def initialize(self) -> None:
        with self._lock:
            self._backup_if_needed()
            with self._connection() as connection:
                # Existing tables keep their original column set, so apply
                # additive migrations before creating indexes on new columns.
                non_index_schema = "\n".join(
                    statement + ";"
                    for statement in SCHEMA.split(";")
                    if "CREATE INDEX" not in statement and statement.strip()
                )
                connection.executescript(non_index_schema)
                paper_columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(papers)").fetchall()
                }
                for name, definition in _PAPER_MIGRATIONS:
                    if name not in paper_columns:
                        connection.execute(f"ALTER TABLE papers ADD COLUMN {name} {definition}")

                search_columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(searches)").fetchall()
                }
                for name, definition in (
                    ("original_query", "TEXT"),
                    ("expanded_queries_json", "TEXT NOT NULL DEFAULT '[]'"),
                    ("filters_json", "TEXT NOT NULL DEFAULT '{}'"),
                    ("total_found", "INTEGER NOT NULL DEFAULT 0"),
                    ("diagnostics_json", "TEXT NOT NULL DEFAULT '{}'"),
                    ("algorithm_version", "TEXT NOT NULL DEFAULT '1'"),
                ):
                    if name not in search_columns:
                        connection.execute(f"ALTER TABLE searches ADD COLUMN {name} {definition}")
                result_columns = {
                    row["name"] for row in connection.execute("PRAGMA table_info(search_results)")
                }
                for name, definition in (
                    ("rank", "INTEGER NOT NULL DEFAULT 0"),
                    ("score", "REAL"),
                    ("score_details_json", "TEXT NOT NULL DEFAULT '{}'"),
                    ("sources_json", "TEXT NOT NULL DEFAULT '[]'"),
                ):
                    if name not in result_columns:
                        connection.execute(f"ALTER TABLE search_results ADD COLUMN {name} {definition}")
                connection.execute(
                    "UPDATE searches SET total_found=result_count WHERE total_found=0"
                )
                for statement in SCHEMA.split(";"):
                    if "CREATE INDEX" in statement and statement.strip():
                        connection.execute(statement)
                connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING "
                    "fts5(internal_id UNINDEXED, title, abstract, topics, tokenize='unicode61 remove_diacritics 2')"
                )
                if int(connection.execute("SELECT COUNT(*) FROM papers_fts").fetchone()[0]) == 0:
                    connection.execute(
                        "INSERT INTO papers_fts(internal_id,title,abstract,topics) "
                        "SELECT internal_id,title,COALESCE(abstract,''),topics_json FROM papers"
                    )
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @staticmethod
    def _paper_params(paper: Paper, *, created_at: str, updated_at: str) -> dict[str, Any]:
        return {
            "internal_id": paper.internal_id,
            "doi": paper.doi,
            "openalex_id": paper.openalex_id,
            "semantic_scholar_id": paper.semantic_scholar_id,
            "oasisbr_id": paper.oasisbr_id,
            "bdtd_id": paper.bdtd_id,
            "title": paper.title,
            "abstract": paper.abstract,
            "year": paper.year,
            "venue": paper.venue,
            "document_type": paper.document_type,
            "institution": paper.institution,
            "language": paper.language,
            "authors_json": _json(paper.authors),
            "citation_count": paper.citation_count,
            "url": paper.url,
            "open_access_url": paper.open_access_url,
            "landing_url": paper.landing_url,
            "full_text_url": paper.full_text_url,
            "access_status": paper.access_status,
            "access_evidence_json": _json(paper.access_evidence),
            "access_verified_at": paper.access_verified_at,
            "verified_url": paper.verified_url,
            "verified_open_access_url": paper.verified_open_access_url,
            "link_status_json": _json(paper.link_status),
            "topics_json": _json(paper.topics),
            "sources_json": _json(paper.sources),
            "source_scores_json": _json(paper.source_scores),
            "metadata_json": _json(paper.metadata),
            "provenance_json": _json(paper.provenance),
            "ranking_score": paper.ranking_score,
            "score_details_json": _json(paper.score_details),
            "ranking_version": paper.ranking_version,
            "created_at": created_at,
            "updated_at": updated_at,
        }

    @staticmethod
    def _find_existing_row(connection: sqlite3.Connection, paper: Paper) -> sqlite3.Row | None:
        conditions: list[str] = []
        values: list[Any] = []
        for column, value in (
            ("internal_id", paper.internal_id),
            ("doi", paper.doi),
            ("openalex_id", paper.openalex_id),
            ("semantic_scholar_id", paper.semantic_scholar_id),
            ("oasisbr_id", paper.oasisbr_id),
            ("bdtd_id", paper.bdtd_id),
        ):
            if value:
                conditions.append(f"{column} = ?")
                values.append(value)
        if not conditions:
            return None
        return connection.execute(
            f"SELECT * FROM papers WHERE {' OR '.join(conditions)} LIMIT 1", values
        ).fetchone()

    def upsert_papers(self, papers: list[Paper]) -> list[Paper]:
        stored: list[Paper] = []
        columns = tuple(
            self._paper_params(Paper("", "placeholder"), created_at="", updated_at="")
        )
        insert_sql = (
            f"INSERT INTO papers ({','.join(columns)}) "
            f"VALUES ({','.join(':' + column for column in columns)})"
        )
        update_columns = [
            column for column in columns if column not in {"internal_id", "created_at"}
        ]
        update_sql = (
            "UPDATE papers SET "
            + ",".join(f"{column}=:{column}" for column in update_columns)
            + " WHERE internal_id=:target_id"
        )
        with self._lock, self._connection() as connection:
            for incoming in papers:
                if not incoming.internal_id:
                    incoming.internal_id = make_internal_id(incoming)
                existing_row = self._find_existing_row(connection, incoming)
                now = _now()
                if existing_row is not None:
                    existing = paper_from_storage(existing_row)
                    merged = merge_papers(existing, incoming)
                    merged.internal_id = existing.internal_id
                    params = self._paper_params(
                        merged, created_at=existing_row["created_at"], updated_at=now
                    )
                    params["target_id"] = existing.internal_id
                    connection.execute(update_sql, params)
                    stored.append(merged)
                else:
                    connection.execute(
                        insert_sql,
                        self._paper_params(incoming, created_at=now, updated_at=now),
                    )
                    stored.append(incoming)
                indexed = stored[-1]
                connection.execute("DELETE FROM papers_fts WHERE internal_id=?", (indexed.internal_id,))
                connection.execute(
                    "INSERT INTO papers_fts(internal_id,title,abstract,topics) VALUES (?,?,?,?)",
                    (indexed.internal_id, indexed.title, indexed.abstract or "", " ".join(indexed.topics)),
                )
        return stored

    def search_local(self, technical_focus: str, *, limit: int = 30) -> list[Paper]:
        """Find previous normalized candidates; callers must recheck PDFs."""
        words = [word for word in re.findall(r"[\w]+", normalize_title(technical_focus)) if len(word) >= 2]
        if not words:
            return []
        expression = " OR ".join(f'"{word}"' for word in words[:4])
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT p.* FROM papers_fts f JOIN papers p ON p.internal_id=f.internal_id "
                "WHERE papers_fts MATCH ? ORDER BY bm25(papers_fts) LIMIT ?",
                (expression, max(1, min(int(limit), 100))),
            ).fetchall()
            return [paper_from_storage(row) for row in rows]

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
        diagnostics: Mapping[str, Any] | None = None,
        algorithm_version: str = "1",
    ) -> int:
        created_at = _now()
        total_found_value = max(len(papers), int(total_found or 0))
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """INSERT INTO searches (
                   cache_key, original_query, expanded_queries_json, filters_json,
                   source_status_json, diagnostics_json, algorithm_version,
                   result_count, total_found, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(cache_key) DO UPDATE SET
                   original_query=excluded.original_query,
                   expanded_queries_json=excluded.expanded_queries_json,
                   filters_json=excluded.filters_json,
                   source_status_json=excluded.source_status_json,
                   diagnostics_json=excluded.diagnostics_json,
                   algorithm_version=excluded.algorithm_version,
                   result_count=excluded.result_count,
                   total_found=excluded.total_found,
                   created_at=excluded.created_at""",
                (
                    cache_key,
                    original_query,
                    _json(expanded_queries),
                    _json(dict(filters)),
                    _json(dict(source_status)),
                    _json(dict(diagnostics or {})),
                    algorithm_version,
                    len(papers),
                    total_found_value,
                    created_at,
                ),
            )
            search_id = cursor.lastrowid or connection.execute(
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

    def get_cached_search(
        self,
        cache_key: str,
        *,
        ttl_hours: float | None = None,
        algorithm_version: str | None = None,
    ) -> dict[str, Any] | None:
        ttl = self.ttl_hours if ttl_hours is None else max(0.0, float(ttl_hours))
        threshold = datetime.now(timezone.utc) - timedelta(hours=ttl)
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM searches WHERE cache_key=? ORDER BY id DESC LIMIT 1",
                (cache_key,),
            ).fetchone()
            if row is None or (algorithm_version and row["algorithm_version"] != algorithm_version):
                return None
            try:
                created_at = datetime.fromisoformat(row["created_at"])
            except (TypeError, ValueError):
                return None
            if created_at < threshold or (int(row["result_count"] or 0) == 0 and
                created_at < datetime.now(timezone.utc) - timedelta(minutes=30)):
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
                paper.score_details = _loads(result_row["result_score_details_json"], {})
                papers.append(paper)
            return {
                "search_id": row["id"],
                "created_at": row["created_at"],
                "expanded_queries": _loads(row["expanded_queries_json"], []),
                "filters": _loads(row["filters_json"], {}),
                "source_status": _loads(row["source_status_json"], {}),
                "diagnostics": _loads(row["diagnostics_json"], {}),
                "algorithm_version": row["algorithm_version"],
                "total_found": max(int(row["total_found"] or 0), len(papers)),
                "papers": papers,
            }

    def save_source_query(
        self,
        *,
        cache_key: str,
        source: str,
        query: str,
        filters: Mapping[str, Any],
        papers: list[Paper],
        status: Mapping[str, Any],
    ) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """INSERT INTO source_query_cache
                   (cache_key, source, query, filters_json, papers_json, status_json, created_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(cache_key) DO UPDATE SET
                     papers_json=excluded.papers_json,
                     status_json=excluded.status_json,
                     created_at=excluded.created_at""",
                (
                    cache_key,
                    source,
                    query,
                    _json(dict(filters)),
                    _json([paper.to_storage_dict() for paper in papers]),
                    _json(dict(status)),
                    _now(),
                ),
            )

    def get_source_query(self, cache_key: str, *, ttl_hours: float) -> dict[str, Any] | None:
        threshold = datetime.now(timezone.utc) - timedelta(hours=max(0.0, ttl_hours))
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM source_query_cache WHERE cache_key=?", (cache_key,)
            ).fetchone()
            if row is None:
                return None
            try:
                created_at = datetime.fromisoformat(row["created_at"])
            except (TypeError, ValueError):
                return None
            if created_at < threshold or (not _loads(row["papers_json"], []) and
                created_at < datetime.now(timezone.utc) - timedelta(minutes=30)):
                return None
            return {
                "papers": [Paper.from_dict(item) for item in _loads(row["papers_json"], [])],
                "status": _loads(row["status_json"], {}),
                "created_at": row["created_at"],
            }

    def save_access_check(self, check: Mapping[str, Any]) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """INSERT INTO access_checks
                   (url, status, final_url, http_status, content_type, reason, evidence_json, checked_at)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(url) DO UPDATE SET
                     status=excluded.status,
                     final_url=excluded.final_url,
                     http_status=excluded.http_status,
                     content_type=excluded.content_type,
                     reason=excluded.reason,
                     evidence_json=excluded.evidence_json,
                     checked_at=excluded.checked_at""",
                (
                    check["url"],
                    check["status"],
                    check.get("final_url"),
                    check.get("http_status"),
                    check.get("content_type"),
                    check.get("reason"),
                    _json(dict(check.get("evidence") or {})),
                    check.get("checked_at") or _now(),
                ),
            )

    def get_access_check(
        self,
        url: str,
        *,
        valid_ttl_hours: float = 168,
        invalid_ttl_hours: float = 24,
        temporary_ttl_hours: float = 1,
    ) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT * FROM access_checks WHERE url=?", (url,)).fetchone()
            if row is None:
                return None
            ttl = (
                valid_ttl_hours
                if row["status"] == "verified_pdf"
                else temporary_ttl_hours
                if row["status"] == "temporary_error"
                else invalid_ttl_hours
            )
            try:
                checked_at = datetime.fromisoformat(row["checked_at"])
            except (TypeError, ValueError):
                return None
            if checked_at < datetime.now(timezone.utc) - timedelta(hours=max(0.0, ttl)):
                return None
            return {
                "url": row["url"],
                "status": row["status"],
                "final_url": row["final_url"],
                "http_status": row["http_status"],
                "content_type": row["content_type"],
                "reason": row["reason"],
                "evidence": _loads(row["evidence_json"], {}),
                "checked_at": row["checked_at"],
            }

    def find_paper(self, identifier: str) -> Paper | None:
        raw = str(identifier or "").strip()
        doi = normalize_doi(raw)
        candidates = [raw]
        if raw.startswith("doi:"):
            candidates.append(raw[4:].strip())
        compact = raw.rstrip("/").rsplit("/", 1)[-1]
        candidates.extend(
            [
                compact,
                compact.removeprefix("openalex:"),
                compact.removeprefix("s2:"),
                compact.removeprefix("oasisbr:"),
                compact.removeprefix("bdtd:"),
            ]
        )
        with self._lock, self._connection() as connection:
            conditions: list[str] = []
            values: list[Any] = []
            if doi:
                conditions.append("doi=?")
                values.append(doi)
            for candidate in dict.fromkeys(candidates):
                for column in (
                    "internal_id",
                    "openalex_id",
                    "semantic_scholar_id",
                    "oasisbr_id",
                    "bdtd_id",
                ):
                    conditions.append(f"{column}=?")
                    values.append(candidate)
            row = connection.execute(
                f"SELECT * FROM papers WHERE {' OR '.join(conditions)} LIMIT 1", values
            ).fetchone()
            return paper_from_storage(row) if row else None

    def stats(self) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            papers = connection.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
            searches = connection.execute("SELECT COUNT(*) FROM searches").fetchone()[0]
            results = connection.execute("SELECT COUNT(*) FROM search_results").fetchone()[0]
            source_queries = connection.execute(
                "SELECT COUNT(*) FROM source_query_cache"
            ).fetchone()[0]
            access_checks = connection.execute("SELECT COUNT(*) FROM access_checks").fetchone()[0]
            fts_papers = connection.execute("SELECT COUNT(*) FROM papers_fts").fetchone()[0]
            last = connection.execute(
                "SELECT created_at, source_status_json FROM searches ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return {
                "database_path": str(self.path),
                "schema_version": int(connection.execute("PRAGMA user_version").fetchone()[0]),
                "migration_backup": self.last_backup_path,
                "papers_cached": int(papers),
                "searches_cached": int(searches),
                "search_results_cached": int(results),
                "source_queries_cached": int(source_queries),
                "access_checks_cached": int(access_checks),
                "papers_fts_indexed": int(fts_papers),
                "last_search_at": last["created_at"] if last else None,
                "last_source_status": _loads(last["source_status_json"], {}) if last else {},
            }


__all__ = ["ResearchStorage", "SCHEMA_VERSION"]
