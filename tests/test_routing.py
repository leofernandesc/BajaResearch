from clients.base import SourceError
from models import Paper
from routing import SearchRouter
from storage import ResearchStorage
from querying import expand_plugin_queries


class CountingClient:
    configured = False

    def __init__(self, papers=None, error=None):
        self.papers = list(papers or [])
        self.error = error
        self.search_calls = 0
        self.queries = []
        self.get_calls = 0

    def search(self, query, **kwargs):
        self.search_calls += 1
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.papers

    def get(self, identifier):
        self.get_calls += 1
        return self.papers[0] if self.papers else None


def test_source_query_cache_avoids_repeating_api_calls(tmp_path):
    paper = Paper(
        "",
        "Projeto de suspensão Baja SAE",
        document_type="bachelorThesis",
        open_access_url="https://repository.example/document.pdf",
        sources=["oasisbr"],
    )
    client = CountingClient([paper])
    router = SearchRouter(
        clients={"oasisbr": client},
        storage=ResearchStorage(tmp_path / "research.sqlite3"),
        cache_ttl_hours=24,
    )
    first = router.search(
        queries=["Baja SAE suspensão"],
        limit=1,
        year_from=None,
        year_to=None,
        prefer_long_form=True,
    )
    second = router.search(
        queries=["Baja SAE suspensão"],
        limit=1,
        year_from=None,
        year_to=None,
        prefer_long_form=True,
    )
    assert client.search_calls == 1
    assert first[0].cache_hit is False
    assert second[0].cache_hit is True
    assert second[0].papers[0].open_access_url.endswith("document.pdf")


def test_rate_limit_opens_circuit_for_remaining_queries(tmp_path):
    client = CountingClient(
        error=SourceError(
            "openalex",
            "source rate limit reached",
            status=429,
            code="rate_limited",
            retryable=True,
        )
    )
    router = SearchRouter(
        clients={"openalex": client},
        storage=ResearchStorage(tmp_path / "research.sqlite3"),
        circuit_breaker_seconds=60,
    )
    first = router.search(
        queries=["query one", "query two"],
        limit=5,
        year_from=None,
        year_to=None,
        prefer_long_form=True,
    )
    second = router.search(
        queries=["query three"],
        limit=5,
        year_from=None,
        year_to=None,
        prefer_long_form=True,
    )
    assert client.search_calls == 1
    assert first[0].error["code"] == "rate_limited"
    assert second[0].error["code"] == "circuit_open"
    assert second[0].skipped is True


def test_crossref_is_not_used_as_a_candidate_search_source(tmp_path):
    openalex = CountingClient(
        [Paper("", "Baja SAE chassis", sources=["openalex"])]
    )
    crossref = CountingClient(
        [Paper("", "Unrelated Crossref result", sources=["crossref"])]
    )
    router = SearchRouter(
        clients={"openalex": openalex, "crossref": crossref},
        storage=ResearchStorage(tmp_path / "research.sqlite3"),
    )
    results = router.search(
        queries=["Baja SAE chassis"],
        limit=3,
        year_from=None,
        year_to=None,
        prefer_long_form=True,
    )
    titles = [paper.title for result in results for paper in result.papers]
    assert "Baja SAE chassis" in titles
    assert "Unrelated Crossref result" not in titles
    assert crossref.search_calls == 0


def test_query_priority_avoids_overconstrained_thesis_suffix():
    prioritized = SearchRouter._prioritized_queries(
        [
            "Baja SAE suspension optimization",
            "Baja SAE suspension optimization institutional repository undergraduate thesis",
            "off-road suspension geometry",
        ]
    )
    assert prioritized[0] == "Baja SAE suspension optimization"


def test_query_expansion_adds_focus_context_when_llm_queries_are_overconstrained():
    expanded = expand_plugin_queries(
        [
            "Baja SAE suspension optimization geometry monograph",
            "Mini Baja otimização suspensão TCC geometria",
        ],
        baja_context=True,
        prefer_theses=True,
        technical_focus="suspension geometry optimization",
    )
    assert "Baja SAE suspension" in expanded
    assert "Baja SAE suspensão" in expanded


def test_repository_query_uses_portuguese_topic_without_pdf_keywords(tmp_path):
    oasis = CountingClient()
    openalex = CountingClient()
    router = SearchRouter(
        clients={"oasisbr": oasis, "openalex": openalex},
        storage=ResearchStorage(tmp_path / "research.sqlite3"),
    )
    queries = expand_plugin_queries(
        ['"Baja SAE" "suspensão" "PDF" "repositório"'],
        baja_context=True, prefer_theses=True,
        technical_focus="suspension geometry optimization finite element analysis",
    )
    router.search(queries=queries, limit=3, year_from=None, year_to=None, prefer_long_form=True)
    assert oasis.queries[0] == "Baja SAE suspensão"
    assert openalex.queries[0] == "Baja SAE suspension"
