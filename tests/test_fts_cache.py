"""Local normalized index is only a retrieval fallback, never access proof."""

from datetime import datetime, timedelta, timezone
import sqlite3

from models import Paper
from storage import ResearchStorage


def test_fts_indexes_and_updates_normalized_papers(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite3")
    paper = Paper("", "Telemetria LoRa em um carro Baja SAE", abstract="Aquisição de dados")
    stored = storage.upsert_papers([paper])[0]
    assert [item.internal_id for item in storage.search_local("lora")] == [stored.internal_id]
    storage.upsert_papers([Paper(stored.internal_id, stored.title, abstract="Suspensão e LoRa usados em experimento de telemetria Baja SAE", sources=["ufscar"])])
    assert storage.search_local("suspensao")
    assert storage.stats()["papers_fts_indexed"] == 1


def test_zero_result_search_expires_before_successful_search(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite3", ttl_hours=24)
    storage.save_search(cache_key="zero", original_query="LoRa", expanded_queries=["LoRa"],
                        filters={}, papers=[], source_status={})
    old = (datetime.now(timezone.utc) - timedelta(minutes=35)).isoformat()
    with sqlite3.connect(storage.path) as connection:
        connection.execute("UPDATE searches SET created_at=? WHERE cache_key='zero'", (old,))
    assert storage.get_cached_search("zero") is None


def test_empty_source_query_expires_after_30_minutes(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite3")
    storage.save_source_query(cache_key="zero", source="openalex", query="LoRa",
                              filters={}, papers=[], status={"status": "ok"})
    old = (datetime.now(timezone.utc) - timedelta(minutes=35)).isoformat()
    with sqlite3.connect(storage.path) as connection:
        connection.execute("UPDATE source_query_cache SET created_at=? WHERE cache_key='zero'", (old,))
    assert storage.get_source_query("zero", ttl_hours=24) is None
