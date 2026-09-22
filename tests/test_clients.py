import httpx

from clients.base import SourceError
from clients.bdtd import BdtdClient
from clients.crossref import CrossrefClient
from clients.http import JsonHttpClient
from clients.link_validator import AccessCheck, LinkValidator
from clients.openalex import OpenAlexClient
from clients.oasisbr import OasisbrClient
from clients.repositories import RepositoryResolver
from clients.semantic_scholar import SemanticScholarClient
from clients.unpaywall import UnpaywallClient
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


def test_oasisbr_normalizes_bachelor_thesis_without_claiming_landing_as_pdf():
    def handler(request):
        assert request.url.path == "/vufind/api/v1/search"
        assert request.url.params["lookfor"] == "Baja SAE suspension"
        return httpx.Response(
            200,
            json={
                "resultCount": 1,
                "records": [
                    {
                        "id": "UNSP_record-1",
                        "title": "Projeto de suspensão dianteira para mini Baja SAE",
                        "authors": {"primary": {"Nogueira, Rodrigo": []}},
                        "formats": ["bachelorThesis"],
                        "languages": ["por"],
                        "subjects": ["Baja SAE", "Suspensão Duplo A"],
                        "urls": [{"url": "http://hdl.handle.net/11449/217462"}],
                        "oai_identifier_st": "oai:repositorio.unesp.br:11449/217462",
                    }
                ],
                "status": "OK",
            },
            request=request,
        )

    raw = JsonHttpClient(
        "https://oasisbr.ibict.br/vufind/api/v1",
        "oasisbr",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    paper = OasisbrClient(http=raw).search("Baja SAE suspension", limit=5)[0]
    assert paper.oasisbr_id == "UNSP_record-1"
    assert paper.document_type == "bachelor_thesis"
    assert paper.authors == ["Nogueira, Rodrigo"]
    assert paper.landing_url == "http://hdl.handle.net/11449/217462"
    assert paper.open_access_url is None
    assert paper.metadata["oai_identifier"].startswith("oai:")


def test_bdtd_normalizes_master_thesis_and_direct_pdf_candidate():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "resultCount": 1,
                "records": [
                    {
                        "id": "UFSC_record-2",
                        "title": "Modelagem dinâmica do Baja SAE",
                        "authors": {"primary": {"Berto, Lucas": []}},
                        "formats": ["masterThesis"],
                        "languages": ["por"],
                        "subjects": ["Dinâmica veicular"],
                        "urls": [
                            {"url": "https://repository.example/item"},
                            {"url": "https://repository.example/document.pdf"},
                        ],
                    }
                ],
                "status": "OK",
            },
            request=request,
        )

    raw = JsonHttpClient(
        "https://bdtd.ibict.br/vufind/api/v1",
        "bdtd",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    paper = BdtdClient(http=raw).search("Baja SAE", limit=5)[0]
    assert paper.bdtd_id == "UFSC_record-2"
    assert paper.document_type == "master_thesis"
    assert paper.open_access_url.endswith("document.pdf")
    assert paper.metadata["full_text_candidates"] == [
        "https://repository.example/document.pdf"
    ]


def test_vufind_zero_results_is_a_successful_empty_search():
    def handler(request):
        return httpx.Response(
            200, json={"resultCount": 0, "status": "OK"}, request=request
        )

    raw = JsonHttpClient(
        "https://bdtd.ibict.br/vufind/api/v1",
        "bdtd",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert BdtdClient(http=raw).search("no matching work") == []


def test_dspace7_repository_resolution_enriches_and_finds_original_pdf():
    item_id = "d64c5b62-e396-41e4-b978-2298433e0595"

    class LandingVerifier:
        def check(self, url):
            return AccessCheck(
                "invalid",
                url,
                final_url=f"https://repository.example/entities/publication/{item_id}",
                http_status=200,
                content_type="text/html",
                reason="response_is_not_pdf",
            )

    def handler(request):
        path = request.url.path
        if path.endswith(f"/items/{item_id}"):
            payload = {
                "metadata": {
                    "dc.description.abstract": [{"value": "Suspension abstract"}],
                    "dc.contributor.institution": [{"value": "Example University"}],
                    "dc.date.issued": [{"value": "2022-03-08"}],
                    "dc.type": [{"value": "Trabalho de conclusão de curso"}],
                    "dc.subject": [{"value": "Baja SAE"}],
                }
            }
        elif path.endswith(f"/items/{item_id}/bundles"):
            payload = {
                "_embedded": {
                    "bundles": [
                        {
                            "name": "ORIGINAL",
                            "_links": {
                                "bitstreams": {
                                    "href": "https://repository.example/server/api/core/bundles/b1/bitstreams"
                                }
                            },
                        }
                    ]
                }
            }
        elif path.endswith("/bundles/b1/bitstreams"):
            payload = {
                "_embedded": {
                    "bitstreams": [
                        {
                            "name": "suspension.pdf",
                            "_links": {
                                "content": {
                                    "href": "https://repository.example/server/api/core/bitstreams/pdf1/content"
                                }
                            },
                        }
                    ]
                }
            }
        else:
            raise AssertionError(path)
        return httpx.Response(200, json=payload, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resolver = RepositoryResolver(LandingVerifier(), http_client=client)
    paper = Paper(
        "",
        "Projeto de suspensão Baja SAE",
        landing_url="http://hdl.handle.net/11449/217462",
        sources=["oasisbr"],
    )
    try:
        candidates = resolver.resolve(paper)
    finally:
        resolver.close()
        client.close()
    assert candidates == [
        "https://repository.example/server/api/core/bitstreams/pdf1/content"
    ]
    assert paper.document_type == "bachelor_thesis"
    assert paper.year == 2022
    assert paper.institution == "Example University"
    assert paper.abstract == "Suspension abstract"


def test_dspace9_oai_resolution_finds_original_pdf_when_server_api_is_private():
    landing = "https://repository.example/handle/20.500.14289/13903"
    pdf = "https://repository.example/bitstreams/pdf-1/download"

    class LandingVerifier:
        def check(self, url):
            return AccessCheck(
                "invalid",
                url,
                final_url=landing,
                http_status=200,
                content_type="text/html",
                reason="response_is_not_pdf",
            )

    def handler(request):
        if request.url.path.endswith("/rest/handle/20.500.14289/13903"):
            return httpx.Response(404, request=request)
        assert request.url.path == "/server/oai/request"
        prefix = request.url.params["metadataPrefix"]
        if prefix == "oai_dc":
            payload = """<?xml version="1.0"?>
                <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"
                  xmlns:dc="http://purl.org/dc/elements/1.1/">
                  <GetRecord><record><metadata><dc:dc>
                    <dc:creator>Author, A.</dc:creator>
                    <dc:description>A long Baja telemetry abstract.</dc:description>
                    <dc:date>2020-12-17</dc:date>
                    <dc:publisher>Example University</dc:publisher>
                    <dc:subject>Baja SAE</dc:subject>
                    <dc:type>TCC</dc:type>
                  </dc:dc></metadata></record></GetRecord>
                </OAI-PMH>"""
            return httpx.Response(200, content=payload.encode(), request=request)
        if prefix == "mets":
            payload = f"""<?xml version="1.0"?>
                <mets:mets xmlns:mets="http://www.loc.gov/METS/"
                  xmlns:xlink="http://www.w3.org/1999/xlink">
                  <mets:fileSec><mets:fileGrp USE="ORIGINAL">
                    <mets:file MIMETYPE="application/pdf">
                      <mets:FLocat xlink:href="{pdf}" />
                    </mets:file>
                  </mets:fileGrp></mets:fileSec>
                </mets:mets>"""
            return httpx.Response(200, content=payload.encode(), request=request)
        return httpx.Response(404, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resolver = RepositoryResolver(LandingVerifier(), http_client=client)
    paper = Paper(
        "",
        "Projeto de telemetria Baja SAE",
        landing_url=landing,
        metadata={"oai_identifier": "oai:repository.example:20.500.14289/13903"},
    )
    try:
        candidates = resolver.resolve(paper)
    finally:
        resolver.close()
        client.close()
    assert candidates == [pdf]
    assert paper.year == 2020
    assert paper.document_type == "bachelor_thesis"
    assert paper.institution == "Example University"
    assert paper.provenance["repository"]["dspace_version"] == 9


def test_unpaywall_returns_candidates_but_does_not_mark_them_verified():
    def handler(request):
        assert request.url.params["email"] == "researcher@example.com"
        return httpx.Response(
            200,
            json={
                "doi": "10.1000/example",
                "is_oa": True,
                "oa_status": "green",
                "best_oa_location": {
                    "url_for_pdf": "https://repository.example/document.pdf",
                    "url": "https://repository.example/item",
                },
            },
            request=request,
        )

    raw = JsonHttpClient(
        "https://api.unpaywall.org/v2",
        "unpaywall",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client = UnpaywallClient(email="researcher@example.com", http=raw)
    result = client.resolve("10.1000/example")
    assert result["candidates"] == ["https://repository.example/document.pdf"]
    assert "verified" not in result


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


def test_pdf_verifier_rejects_html_mislabeled_as_pdf():
    def handler(request):
        return httpx.Response(
            200, headers={"Content-Type": "application/pdf"},
            content=b"<html>Access denied</html>", request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    verifier = LinkValidator(http_client=client, resolver=lambda _host, _port: ["8.8.8.8"])
    try:
        result = verifier.check("https://repository.example/document.pdf")
    finally:
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
    openalex_paper = Paper("", "Baja SAE telemetry data acquisition", year=2024, open_access_url="https://repository.example/telemetry.pdf", sources=["openalex"], source_scores={"openalex": .8})
    crossref_paper = Paper("", "Baja SAE telemetry data acquisition", year=2024, doi="10.1000/telemetry", sources=["crossref"], source_scores={"crossref": .35})
    s2_error = SourceError("semantic_scholar", "source rate limit reached", status=429, code="rate_limited", retryable=True)
    clients = {
        "openalex": _FakeClient([openalex_paper]),
        "semantic_scholar": _FakeClient(error=s2_error),
        "crossref": _FakeClient([crossref_paper]),
    }
    class AcceptAllPdfVerifier:
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

        def close(self):
            pass

    service = ResearchService(
        config=ResearchConfig(cache_ttl_hours=24),
        storage=ResearchStorage(tmp_path / "cache.sqlite3"),
        clients=clients,
        link_validator=AcceptAllPdfVerifier(),
    )
    result = service.search(
        queries=["Baja SAE telemetry"], technical_focus="telemetry data acquisition", limit=5
    )
    assert result["ok"] is True
    assert result["returned"] == 1
    assert result["sources"]["semantic_scholar"]["status"] == "error"
    assert result["sources"]["semantic_scholar"]["errors"][0]["code"] == "rate_limited"
    assert result["warnings"]
