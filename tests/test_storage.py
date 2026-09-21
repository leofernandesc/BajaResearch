from models import Paper
from storage import ResearchStorage


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
    )
    cached = storage.get_cached_search("same-search")
    assert cached is not None
    assert cached["papers"][0].doi == "10.1000/chassis"
    assert cached["papers"][0].score_details["query_relevance"] == 0.9
    assert cached["total_found"] == 7
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
