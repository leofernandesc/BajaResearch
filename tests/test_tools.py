import json

from models import Paper
from storage import ResearchStorage
from tools import ResearchConfig, ResearchService, build_tool_handlers


class FakeClient:
    configured = False

    def __init__(self):
        self.calls = 0
        self.paper = Paper("", "Formula Student chassis", year=2022, doi="10.1000/formula", authors=["A. Author"], sources=["openalex"], source_scores={"openalex": .6})
        self.papers = [self.paper]

    def search(self, query, **kwargs):
        self.calls += 1
        return self.papers

    def get(self, identifier):
        return self.papers[0]

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
    args = {"queries": ["Formula Student chassis"], "limit": 2, "open_access_only": False}
    first = json.loads(handlers["search_academic_papers"](args))
    second = json.loads(handlers["search_academic_papers"](args))
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
    result = service.search(queries=["electronics"], limit=1, open_access_only=False)
    assert "Baja SAE electronics" in result["queries"]
    assert "electronics off-road vehicle Formula SAE" in result["queries"]
    assert any("thesis" in query for query in result["queries"])
    assert result["filters"]["baja_context"] is True
    assert result["filters"]["prefer_theses"] is True


def test_electric_vehicle_results_are_excluded_by_default(tmp_path):
    ev = Paper(
        "",
        "Battery electric vehicle telemetry and energy management",
        abstract="Charging, battery and powertrain control for an electric vehicle.",
        year=2023,
        sources=["openalex"],
        source_scores={"openalex": 0.8},
    )
    baja = Paper(
        "",
        "Baja SAE vehicle telemetry and data acquisition",
        abstract="Telemetry and sensors for an off-road Baja vehicle.",
        year=2023,
        sources=["openalex"],
        source_scores={"openalex": 0.7},
    )
    ev_pt = Paper(
        "",
        "Análise estrutural de veículo elétrico Baja SAE",
        abstract="Projeto de bateria e powertrain para mobilidade elétrica.",
        year=2023,
        sources=["openalex"],
    )
    clients = {
        name: FakeClient()
        for name in ("openalex", "semantic_scholar", "crossref")
    }
    for client in clients.values():
        client.paper = baja
        client.papers = [ev, ev_pt, baja]
    service = ResearchService(
        config=ResearchConfig(cache_ttl_hours=0),
        storage=ResearchStorage(tmp_path / "cache.sqlite3"),
        clients=clients,
    )
    result = service.search(queries=["electronics"], limit=5, open_access_only=False)
    titles = [item["title"] for item in result["results"]]
    assert baja.title in titles
    assert ev.title not in titles
    assert ev_pt.title not in titles
    assert result["filters"]["exclude_electric_vehicles"] is True
    assert result["filters_applied"]["electric_vehicle"] > 0


def test_open_access_is_the_default_recommendation_filter(tmp_path):
    open_paper = Paper(
        "",
        "Baja SAE telemetry system thesis",
        year=2022,
        open_access_url="https://repository.example/thesis",
        sources=["openalex"],
    )
    paywalled = Paper(
        "",
        "Baja SAE telemetry journal article",
        year=2023,
        url="https://publisher.example/paywall",
        sources=["crossref"],
    )
    clients = {
        name: FakeClient()
        for name in ("openalex", "semantic_scholar", "crossref")
    }
    for client in clients.values():
        client.papers = [open_paper, paywalled]
    service = ResearchService(
        config=ResearchConfig(cache_ttl_hours=0, validate_links=False),
        storage=ResearchStorage(tmp_path / "cache.sqlite3"),
        clients=clients,
    )
    result = service.search(queries=["Baja SAE telemetry"], limit=5)
    titles = [item["title"] for item in result["results"]]
    assert result["filters"]["open_access_only"] is True
    assert open_paper.title in titles
    assert paywalled.title not in titles


def test_unreachable_open_access_link_is_not_recommended(tmp_path):
    open_paper = Paper(
        "",
        "Baja SAE suspension thesis",
        year=2022,
        open_access_url="https://repository.example/thesis.pdf",
        sources=["openalex"],
    )
    unavailable = Paper(
        "",
        "Baja SAE suspension journal article",
        year=2023,
        open_access_url="https://publisher.example/article.pdf",
        sources=["crossref"],
    )
    clients = {
        name: FakeClient()
        for name in ("openalex", "semantic_scholar", "crossref")
    }
    for client in clients.values():
        client.papers = [open_paper, unavailable]

    class StubLinkValidator:
        def check_many(self, urls):
            return {
                url: type(
                    "Check",
                    (),
                    {
                        "status": "valid" if url.endswith("thesis.pdf") else "invalid",
                        "final_url": url,
                    },
                )()
                for url in urls
            }

        def close(self):
            pass

    service = ResearchService(
        config=ResearchConfig(cache_ttl_hours=0),
        storage=ResearchStorage(tmp_path / "cache.sqlite3"),
        clients=clients,
        link_validator=StubLinkValidator(),
    )
    result = service.search(queries=["Baja SAE suspension"], limit=5)
    titles = [item["title"] for item in result["results"]]
    assert open_paper.title in titles
    assert unavailable.title not in titles
    assert result["filters_applied"]["unverified_open_access"] > 0
