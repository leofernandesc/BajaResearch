import json

from models import Paper
from clients.link_validator import AccessCheck
from storage import ResearchStorage
from tools import ResearchConfig, ResearchService, build_tool_handlers


class FakeClient:
    configured = False

    def __init__(self):
        self.calls = 0
        self.paper = Paper("", "Formula Student chassis", year=2022, doi="10.1000/formula", authors=["A. Author"], open_access_url="https://repository.example/formula.pdf", sources=["openalex"], source_scores={"openalex": .6})
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


class AcceptAllPdfVerifier:
    def check_many(self, urls):
        return {
            url: AccessCheck(
                "verified_pdf",
                url,
                final_url=url,
                http_status=206,
                content_type="application/pdf",
                evidence={"pdf_magic": True, "anonymous": True},
            )
            for url in urls
        }

    def check(self, url):
        return self.check_many([url])[url]

    def close(self):
        pass


def test_tool_handlers_return_compact_json_and_cache_hits(tmp_path):
    clients = {name: FakeClient() for name in ("openalex", "semantic_scholar", "crossref")}
    service = ResearchService(
        config=ResearchConfig(cache_ttl_hours=24),
        storage=ResearchStorage(tmp_path / "cache.sqlite3"),
        clients=clients,
        link_validator=AcceptAllPdfVerifier(),
    )
    handlers = {name: handler for name, _schema, handler, _description in build_tool_handlers(service)}
    args = {
        "queries": ["Formula Student chassis"],
        "technical_focus": "chassis",
        "limit": 2,
    }
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
    missing_focus = json.loads(
        handlers["search_academic_papers"]({"queries": ["Baja SAE chassis"]})
    )
    assert missing_focus["error"]["code"] == "invalid_arguments"


def test_broad_query_gets_baja_and_thesis_retrieval_variants(tmp_path):
    clients = {name: FakeClient() for name in ("openalex", "semantic_scholar", "crossref")}
    service = ResearchService(
        config=ResearchConfig(cache_ttl_hours=24),
        storage=ResearchStorage(tmp_path / "cache.sqlite3"),
        clients=clients,
        link_validator=AcceptAllPdfVerifier(),
    )
    result = service.search(queries=["electronics"], technical_focus="electronics telemetry", limit=1)
    assert "Baja SAE electronics" in result["queries"]
    assert "electronics off-road vehicle Formula SAE" in result["queries"]
    assert any("thesis" in query for query in result["queries"])
    assert result["filters"]["baja_context_required"] is True
    assert result["filters"]["document_preference"] == "long_form_first"


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
        open_access_url="https://repository.example/baja-telemetry.pdf",
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
        link_validator=AcceptAllPdfVerifier(),
    )
    result = service.search(
        queries=["electronics"], technical_focus="electronics telemetry", limit=5
    )
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
    class StubPdfVerifier:
        def check_many(self, urls):
            return {
                url: AccessCheck(
                    "verified_pdf",
                    url,
                    final_url=url,
                    http_status=200,
                    content_type="application/pdf",
                    evidence={"pdf_magic": True},
                )
                for url in urls
            }

        def close(self):
            pass

    service = ResearchService(
        config=ResearchConfig(cache_ttl_hours=0),
        storage=ResearchStorage(tmp_path / "cache.sqlite3"),
        clients=clients,
        link_validator=StubPdfVerifier(),
    )
    result = service.search(queries=["Baja SAE telemetry"], limit=5)
    titles = [item["title"] for item in result["results"]]
    assert result["filters"]["free_full_text_only"] is True
    assert open_paper.title in titles
    assert paywalled.title not in titles


def test_free_pdf_policy_cannot_be_disabled_by_direct_legacy_flag(tmp_path):
    paywalled = Paper(
        "",
        "Baja SAE suspension optimization",
        document_type="article",
        url="https://publisher.example/paywall",
        sources=["openalex"],
    )
    clients = {name: FakeClient() for name in ("openalex", "semantic_scholar", "crossref")}
    for client in clients.values():
        client.papers = [paywalled]
    service = ResearchService(
        config=ResearchConfig(cache_ttl_hours=0),
        storage=ResearchStorage(tmp_path / "cache.sqlite3"),
        clients=clients,
    )
    result = service.search(
        queries=["Baja SAE suspension optimization"],
        technical_focus="suspension",
        limit=3,
        open_access_only=False,
    )
    assert result["returned"] == 0
    assert result["policy"]["free_full_text_only"] is True


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
                url: AccessCheck(
                    "verified_pdf" if url.endswith("thesis.pdf") else "invalid",
                    url,
                    final_url=url,
                    http_status=200 if url.endswith("thesis.pdf") else 404,
                    content_type="application/pdf" if url.endswith("thesis.pdf") else None,
                    evidence={"pdf_magic": url.endswith("thesis.pdf")},
                )
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


def test_temporary_pdf_failure_keeps_structured_access_status(tmp_path):
    class TemporaryPdfVerifier:
        def check_many(self, urls):
            return {
                url: AccessCheck(
                    "temporary_error",
                    url,
                    final_url=url,
                    http_status=429,
                    reason="temporarily_unavailable",
                )
                for url in urls
            }

        def close(self):
            pass

    paper = Paper(
        "",
        "Projeto de suspensão para Baja SAE",
        open_access_url="https://repository.example/rate-limited.pdf",
    )
    service = ResearchService(
        storage=ResearchStorage(tmp_path / "cache.sqlite3"),
        clients={},
        link_validator=TemporaryPdfVerifier(),
    )
    service._validate_papers([paper])
    assert paper.access_status == "temporary_error"
    assert paper.full_text_url is None


def test_repository_metadata_is_resolved_before_context_gate(tmp_path):
    candidate = Paper(
        "",
        "Projeto de um sistema embarcado de aquisição de dados e telemetria",
        document_type="TCC",
        landing_url="https://repository.example/handle/123",
        sources=["oasisbr"],
    )
    client = FakeClient()
    client.papers = [candidate]

    class RepositoryMetadata:
        def resolve(self, paper):
            paper.abstract = "Aquisição de dados e telemetria para um veículo Baja SAE."
            paper.topics = ["Baja SAE", "Telemetria"]
            paper.institution = "Example University"
            paper.metadata["full_text_candidates"] = [
                "https://repository.example/bitstreams/paper.pdf"
            ]
            return list(paper.metadata["full_text_candidates"])

        def close(self):
            pass

    service = ResearchService(
        config=ResearchConfig(cache_ttl_hours=0),
        storage=ResearchStorage(tmp_path / "cache.sqlite3"),
        clients={"oasisbr": client},
        link_validator=AcceptAllPdfVerifier(),
        repository_resolver=RepositoryMetadata(),
    )
    result = service.search(
        queries=["Baja SAE telemetry data acquisition"],
        technical_focus="telemetry data acquisition",
        limit=1,
    )
    assert result["returned"] == 1
    assert result["results"][0]["institution"] == "Example University"
    assert result["results"][0]["access_status"] == "verified_pdf"


def test_long_form_verified_work_is_listed_before_article(tmp_path):
    thesis = Paper(
        "",
        "Projeto de suspensão para protótipo Baja SAE",
        document_type="bachelorThesis",
        open_access_url="https://repository.example/thesis.pdf",
        sources=["oasisbr"],
        source_scores={"oasisbr": 0.5},
    )
    article = Paper(
        "",
        "Suspension optimization for a Baja SAE off-road vehicle",
        document_type="article",
        citation_count=500,
        open_access_url="https://journal.example/article.pdf",
        sources=["openalex"],
        source_scores={"openalex": 1.0},
    )
    clients = {
        name: FakeClient()
        for name in ("oasisbr", "bdtd", "openalex", "semantic_scholar", "crossref")
    }
    for client in clients.values():
        client.papers = [article, thesis]

    class StubPdfVerifier:
        def check_many(self, urls):
            return {
                url: AccessCheck(
                    "verified_pdf",
                    url,
                    final_url=url,
                    http_status=206,
                    content_type="application/pdf",
                    evidence={"pdf_magic": True},
                )
                for url in urls
            }

        def check(self, url):
            return self.check_many([url])[url]

        def close(self):
            pass

    service = ResearchService(
        config=ResearchConfig(cache_ttl_hours=0),
        storage=ResearchStorage(tmp_path / "cache.sqlite3"),
        clients=clients,
        link_validator=StubPdfVerifier(),
    )
    result = service.search(
        queries=["Baja SAE suspension optimization"],
        technical_focus="suspension optimization",
        limit=2,
    )
    assert [paper["title"] for paper in result["results"]] == [
        thesis.title,
        article.title,
    ]
    assert all(paper["access_status"] == "verified_pdf" for paper in result["results"])
    assert "verified_free_pdf" in result["results"][0]["relevance_reasons"]

    articles_first = service.search(
        queries=["Baja SAE suspension optimization"],
        technical_focus="suspension optimization",
        document_preference="articles_first",
        limit=2,
        refresh_cache=True,
    )
    assert [paper["title"] for paper in articles_first["results"]] == [
        article.title,
        thesis.title,
    ]
