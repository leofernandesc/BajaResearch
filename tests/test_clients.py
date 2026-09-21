import httpx

from clients.base import SourceError
from clients.crossref import CrossrefClient
from clients.http import JsonHttpClient
from clients.link_validator import AccessCheck, LinkValidator
from clients.openalex import OpenAlexClient
from clients.semantic_scholar import SemanticScholarClient
from models import Paper
from ranking import rank_papers
from storage import ResearchStorage
from tools import ResearchConfig, ResearchService


def test_http_client_retries_429_then_returns_json():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "slow down"}, request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    http = JsonHttpClient("https://example.test", "test", http_client=client, max_retries=1, sleep_fn=lambda _: None)
    assert http.get_json("/resource") == {"ok": True}
    assert len(calls) == 2


def test_http_client_surfaces_final_429_structurally():
    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "3"}, json={}, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    http = JsonHttpClient("https://example.test", "semantic_scholar", http_client=client, max_retries=0, sleep_fn=lambda _: None)
    try:
        http.get_json("/resource")
    except SourceError as error:
        assert error.status == 429
        assert error.code == "rate_limited"
        assert error.retryable is True
    else:
        raise AssertionError("expected SourceError")


def test_openalex_normalizes_work_payload():
    def handler(request):
        assert request.url.path == "/works"
        assert request.url.params["filter"] == "from_publication_year:2020,to_publication_year:2024,open_access.is_oa:true"
        return httpx.Response(200, json={"results": [{
            "id": "https://openalex.org/W1",
            "title": "Tubular chassis",
            "publication_year": 2022,
            "doi": "https://doi.org/10.1000/xyz",
            "authorships": [{"author": {"display_name": "A. Author"}}],
            "abstract_inverted_index": {"A": [0], "useful": [1], "abstract": [2]},
            "primary_location": {"source": {"display_name": "Vehicle Journal"}, "landing_page_url": "https://example.test/paper"},
            "best_oa_location": {"is_oa": True, "pdf_url": "https://example.test/paper.pdf"},
            "open_access": {"is_oa": True},
            "cited_by_count": 12,
            "topics": [{"display_name": "Structural engineering"}],
            "relevance_score": 2.0,
        }]}, request=request)

    http = JsonHttpClient("https://api.openalex.org", "openalex", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    paper = OpenAlexClient(http=http).search("tubular chassis", limit=5, year_from=2020, year_to=2024, open_access_only=True)[0]
    assert paper.doi == "10.1000/xyz"
    assert paper.openalex_id == "W1"
    assert paper.abstract == "A useful abstract"
    assert paper.open_access_url.endswith(".pdf")
    assert paper.source_scores["openalex"] == 2 / 3


def test_semantic_scholar_uses_optional_api_key_header():
    def handler(request):
        assert request.headers["x-api-key"] == "secret-not-logged"
        return httpx.Response(200, json={"data": [{
            "paperId": "S1", "title": "Telemetry acquisition", "year": 2023,
            "authors": [{"name": "A. Author"}], "citationCount": 7,
            "externalIds": {"DOI": "10.1000/telemetry"},
            "openAccessPdf": {"url": "https://example.test/telemetry.pdf"},
        }]}, request=request)

    raw = JsonHttpClient("https://api.semanticscholar.org/graph/v1", "semantic_scholar", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    client = SemanticScholarClient(api_key="secret-not-logged", http=raw)
    paper = client.search("telemetry", limit=3)[0]
    assert paper.semantic_scholar_id == "S1"
    assert paper.doi == "10.1000/telemetry"
    assert paper.open_access_url.endswith(".pdf")


def test_crossref_does_not_claim_open_access_without_license():
    def handler(request):
        assert request.url.params["mailto"] == "researcher@example.com"
        return httpx.Response(200, json={"message": {"items": [{
            "DOI": "10.1000/crossref", "title": ["Crossref paper"],
            "author": [{"given": "A", "family": "Author"}], "issued": {"date-parts": [[2020]]},
            "container-title": ["Journal"], "link": [{"URL": "https://example.test/paper.pdf", "content-type": "application/pdf"}],
        }]}}, request=request)

    raw = JsonHttpClient("https://api.crossref.org", "crossref", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    paper = CrossrefClient(mailto="researcher@example.com", http=raw).search("crossref", limit=1)[0]
    assert paper.doi == "10.1000/crossref"
    assert paper.open_access_url is None


def test_pdf_verifier_rejects_dead_link_and_accepts_pdf_bytes():
    calls = []

    def handler(request):
        calls.append((request.method, str(request.url)))
        if request.url.path == "/dead":
            return httpx.Response(404, request=request)
        return httpx.Response(
            206,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF-1.7 test",
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    validator = LinkValidator(
        http_client=client, resolver=lambda _host, _port: ["8.8.8.8"]
    )
    try:
        dead = validator.check("https://example.test/dead")
        live = validator.check("https://example.test/live")
    finally:
        validator.close()
        client.close()
    assert dead.status == "invalid"
    assert dead.http_status == 404
    assert live.status == "verified_pdf"
    assert live.http_status == 206
    assert ("GET", "https://example.test/live") in calls


def test_pdf_verifier_rejects_publisher_html_landing_page():
    def handler(request):
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            content=b"<html><title>Buy access</title></html>",
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    validator = LinkValidator(
        http_client=client, resolver=lambda _host, _port: ["8.8.8.8"]
    )
    try:
        result = validator.check("https://publisher.example/article")
    finally:
        validator.close()
        client.close()
    assert result.status == "invalid"
    assert result.reason == "response_is_not_pdf"


def test_pdf_verifier_blocks_private_redirect_target():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(
            302, headers={"Location": "http://127.0.0.1/private.pdf"}, request=request
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    validator = LinkValidator(
        http_client=client,
        resolver=lambda host, _port: ["127.0.0.1" if host == "127.0.0.1" else "8.8.8.8"],
    )
    try:
        result = validator.check("https://repository.example/document")
    finally:
        validator.close()
        client.close()
    assert result.status == "blocked"
    assert result.reason == "private_network_target"
    assert calls == ["https://repository.example/document"]


def test_pdf_verification_evidence_is_reused_from_sqlite(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF-1.7 cached",
            request=request,
        )

    storage = ResearchStorage(tmp_path / "research.sqlite3")
    first_client = httpx.Client(transport=httpx.MockTransport(handler))
    first = LinkValidator(
        http_client=first_client,
        resolver=lambda _host, _port: ["8.8.8.8"],
        storage=storage,
    )
    try:
        assert first.check("https://repository.example/document.pdf").status == "verified_pdf"
    finally:
        first.close()
        first_client.close()

    def should_not_run(_request):
        raise AssertionError("persistent access cache was not used")

    second_client = httpx.Client(transport=httpx.MockTransport(should_not_run))
    second = LinkValidator(
        http_client=second_client,
        resolver=lambda _host, _port: ["8.8.8.8"],
        storage=storage,
    )
    try:
        cached = second.check("https://repository.example/document.pdf")
    finally:
        second.close()
        second_client.close()
    assert cached.status == "verified_pdf"
    assert calls == ["https://repository.example/document.pdf"]


class _FakeClient:
    def __init__(self, papers=None, error=None):
        self.papers = papers or []
        self.error = error
        self.calls = 0
        self.configured = False

    def search(self, query, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.papers

    def get(self, identifier):
        if self.error:
            raise self.error
        return self.papers[0] if self.papers else None

    def close(self):
        pass


def test_partial_source_failure_keeps_results_and_reports_429(tmp_path):
    openalex_paper = Paper("", "Baja SAE telemetry data acquisition", year=2024, sources=["openalex"], source_scores={"openalex": .8})
    crossref_paper = Paper("", "Baja SAE telemetry data acquisition", year=2024, doi="10.1000/telemetry", sources=["crossref"], source_scores={"crossref": .35})
    s2_error = SourceError("semantic_scholar", "source rate limit reached", status=429, code="rate_limited", retryable=True)
    clients = {
        "openalex": _FakeClient([openalex_paper]),
        "semantic_scholar": _FakeClient(error=s2_error),
        "crossref": _FakeClient([crossref_paper]),
    }
    service = ResearchService(
        config=ResearchConfig(cache_ttl_hours=24),
        storage=ResearchStorage(tmp_path / "cache.sqlite3"),
        clients=clients,
    )
    result = service.search(
        queries=["Baja SAE telemetry"], limit=5, open_access_only=False
    )
    assert result["ok"] is True
    assert result["returned"] == 1
    assert result["sources"]["semantic_scholar"]["status"] == "error"
    assert result["sources"]["semantic_scholar"]["errors"][0]["code"] == "rate_limited"
    assert result["warnings"]
