from models import Paper
from storage import ResearchStorage
import sqlite3


def test_cache_round_trip_and_paper_lookup(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite3", ttl_hours=24)
    paper = Paper(
        "",
        "Baja chassis design",
        authors=["A. Author"],
        year=2023,
        doi="10.1000/chassis",
        sources=["openalex"],
        source_scores={"openalex": 0.7},
        ranking_score=0.8,
        score_details={"query_relevance": 0.9},
    )
    paper = storage.upsert_papers([paper])[0]
    storage.save_search(
        cache_key="same-search",
        original_query="chassis",
        expanded_queries=["Baja chassis design"],
        filters={"limit": 5},
        papers=[paper],
        source_status={"openalex": {"status": "ok"}},
        total_found=7,
        diagnostics={"filter_counts": {"wrong_technical_focus": 2}},
        algorithm_version="test-v2",
    )
    cached = storage.get_cached_search("same-search", algorithm_version="test-v2")
    assert cached is not None
    assert cached["papers"][0].doi == "10.1000/chassis"
    assert cached["papers"][0].score_details["query_relevance"] == 0.9
    assert cached["total_found"] == 7
    assert cached["diagnostics"]["filter_counts"]["wrong_technical_focus"] == 2
    assert storage.get_cached_search("same-search", algorithm_version="old") is None
    assert storage.find_paper("https://doi.org/10.1000/CHASSIS").title == "Baja chassis design"
    assert storage.stats()["papers_cached"] == 1


def test_cache_ttl_zero_is_expired(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite3", ttl_hours=0)
    paper = storage.upsert_papers([Paper("", "A paper", year=2020)])[0]
    storage.save_search(
        cache_key="expired",
        original_query=None,
        expanded_queries=["a paper"],
        filters={},
        papers=[paper],
        source_status={},
    )
    assert storage.get_cached_search("expired", ttl_hours=0) is None


def test_new_access_and_repository_fields_round_trip(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite3")
    paper = Paper(
        "",
        "Projeto de suspensão Baja SAE",
        document_type="bachelorThesis",
        institution="Universidade Exemplo",
        language="por",
        oasisbr_id="oasis-1",
        full_text_url="https://repository.example/document.pdf",
        access_status="verified_pdf",
        access_evidence={"content_type": "application/pdf"},
        access_verified_at="2026-09-21T00:00:00+00:00",
        sources=["oasisbr"],
        provenance={"oasisbr": {"record_id": "oasis-1"}},
        ranking_version="2",
    )
    stored = storage.upsert_papers([paper])[0]
    loaded = storage.find_paper(stored.internal_id)
    assert loaded is not None
    assert loaded.document_type == "bachelor_thesis"
    assert loaded.institution == "Universidade Exemplo"
    assert loaded.access_status == "verified_pdf"
    assert loaded.public_full_text_url().endswith("document.pdf")
    assert loaded.provenance["oasisbr"]["record_id"] == "oasis-1"
    assert storage.stats()["schema_version"] == 4


def test_source_query_and_access_check_cache(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite3")
    paper = Paper("", "Baja SAE suspension", sources=["oasisbr"])
    storage.save_source_query(
        cache_key="source-key",
        source="oasisbr",
        query="Baja SAE suspension",
        filters={"limit": 10},
        papers=[paper],
        status={"status": "ok"},
    )
    cached = storage.get_source_query("source-key", ttl_hours=24)
    assert cached is not None
    assert cached["papers"][0].title == paper.title

    storage.save_access_check(
        {
            "url": "https://repository.example/document.pdf",
            "status": "verified_pdf",
            "final_url": "https://repository.example/document.pdf",
            "http_status": 206,
            "content_type": "application/pdf",
            "evidence": {"pdf_magic": True},
        }
    )
    access = storage.get_access_check("https://repository.example/document.pdf")
    assert access is not None
    assert access["status"] == "verified_pdf"
    assert access["evidence"]["pdf_magic"] is True


def test_existing_v1_database_is_backed_up_and_migrated(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE papers (
            internal_id TEXT PRIMARY KEY, doi TEXT, openalex_id TEXT,
            semantic_scholar_id TEXT, title TEXT NOT NULL, year INTEGER
        );
        CREATE TABLE searches (
            id INTEGER PRIMARY KEY, cache_key TEXT UNIQUE, result_count INTEGER DEFAULT 0,
            created_at TEXT, source_status_json TEXT
        );
        CREATE TABLE search_results (
            search_id INTEGER, paper_id TEXT, PRIMARY KEY(search_id, paper_id)
        );
        PRAGMA user_version = 1;
        """
    )
    connection.close()

    storage = ResearchStorage(path)
    assert storage.last_backup_path is not None
    assert (tmp_path / storage.last_backup_path.rsplit("/", 1)[-1]).exists()
    connection = sqlite3.connect(path)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(papers)")}
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    connection.close()
    assert {"full_text_url", "access_status", "oasisbr_id", "bdtd_id"} <= columns
    assert version == 4
