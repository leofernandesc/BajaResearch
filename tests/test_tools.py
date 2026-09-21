import json

from models import Paper
from storage import ResearchStorage
from tools import ResearchConfig, ResearchService, build_tool_handlers


class FakeClient:
    configured = False

    def __init__(self):
        self.calls = 0
        self.paper = Paper("", "Formula Student chassis", year=2022, doi="10.1000/formula", authors=["A. Author"], sources=["openalex"], source_scores={"openalex": .6})

    def search(self, query, **kwargs):
        self.calls += 1
        return [self.paper]

    def get(self, identifier):
        return self.paper

    def related(self, identifier, **kwargs):
        return []

    def close(self):
        pass


def test_tool_handlers_return_compact_json_and_cache_hits(tmp_path):
    clients = {name: FakeClient() for name in ("openalex", "semantic_scholar", "crossref")}
    service = ResearchService(
        config=ResearchConfig(cache_ttl_hours=24),
        storage=ResearchStorage(tmp_path / "cache.sqlite3"),
        clients=clients,
    )
    handlers = {name: handler for name, _schema, handler, _description in build_tool_handlers(service)}
    first = json.loads(handlers["search_academic_papers"]({"queries": ["Formula Student chassis"], "limit": 2}))
    second = json.loads(handlers["search_academic_papers"]({"queries": ["Formula Student chassis"], "limit": 2}))
    assert first["ok"] is True
    assert second["cache"]["hit"] is True
    assert first["results"][0]["title"] == "Formula Student chassis"
    assert "metadata" not in first["results"][0]
    assert json.loads(handlers["format_citation"]({"identifier": "10.1000/formula", "style": "bibtex"}))["citation"].startswith("@article{")
    assert json.loads(handlers["research_cache_stats"]({}))["papers_cached"] == 1


def test_invalid_tool_arguments_are_structured():
    service = ResearchService(storage=ResearchStorage(":memory:"), clients={})
    # :memory: is only used for validation because no source call is reached.
    handlers = {name: handler for name, _schema, handler, _description in build_tool_handlers(service)}
    response = json.loads(handlers["search_academic_papers"]({"queries": []}))
    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_arguments"


def test_broad_query_gets_baja_and_thesis_retrieval_variants(tmp_path):
    clients = {name: FakeClient() for name in ("openalex", "semantic_scholar", "crossref")}
    service = ResearchService(
        config=ResearchConfig(cache_ttl_hours=24),
        storage=ResearchStorage(tmp_path / "cache.sqlite3"),
        clients=clients,
    )
    result = service.search(queries=["electronics"], limit=1)
    assert "Baja SAE electronics" in result["queries"]
    assert "electronics off-road vehicle Formula SAE" in result["queries"]
    assert any("thesis" in query for query in result["queries"])
    assert result["filters"]["baja_context"] is True
    assert result["filters"]["prefer_theses"] is True
