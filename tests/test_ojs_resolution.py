"""Publisher-page repair requires an exact bibliographic match."""

import httpx

from clients.ojs import OjsPublisherResolver
from clients.link_validator import AccessCheck
from models import Paper
from storage import ResearchStorage
from tools import ResearchConfig, ResearchService


TITLE = "Development of Telemetry system for Student Formula Cars and All-terrain vehicles"
DOI = "10.47392/irjash.2022.035"
OLD = "https://rspsciencehub.com/article_18386_old.pdf"
PDF = "https://rspsciencehub.com/index.php/journal/article/download/591/494"
VIEW = "https://rspsciencehub.com/index.php/journal/article/view/591"


def _client(*, doi=DOI, title=TITLE):
    def respond(request):
        if "/search/search" in str(request.url):
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  text=f'<a href="{VIEW}">Result</a>')
        if "/article/view/591" in str(request.url):
            return httpx.Response(200, headers={"content-type": "text/html"}, text=(
                f'<meta name="citation_doi" content="{doi}">'
                f'<meta name="citation_title" content="{title}">'
                '<meta name="citation_abstract" content="LoRa telemetry on student Formula and ATV vehicles.">'
                f'<meta name="citation_pdf_url" content="{PDF}">'
            ))
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(respond))


def _paper():
    return Paper("", TITLE, doi=DOI, open_access_url=OLD, sources=["openalex"])


def test_ojs_repair_recovers_lora_abstract_and_current_pdf():
    paper = _paper()
    resolver = OjsPublisherResolver(object(), http_client=_client())
    assert resolver.resolve(paper) == [PDF]
    assert "LoRa" in paper.abstract
    assert paper.open_access_url == PDF
    assert paper.landing_url == VIEW
    assert resolver.resolve(paper) == []


def test_ojs_repair_rejects_mismatched_doi_or_title():
    for kwargs in ({"doi": "10.1000/wrong"}, {"title": "Unrelated paper"}):
        paper = _paper()
        resolver = OjsPublisherResolver(object(), http_client=_client(**kwargs))
        assert resolver.resolve(paper) == []
        assert paper.open_access_url == OLD
        assert paper.abstract is None


def test_ojs_repair_ignores_non_publisher_url():
    paper = Paper("", TITLE, doi=DOI, open_access_url="https://example.org/old.pdf")
    resolver = OjsPublisherResolver(object(), http_client=_client())
    assert resolver.resolve(paper) == []


def test_stale_ojs_link_is_promoted_only_after_pdf_check(tmp_path):
    class Source:
        configured = False

        def search(self, _query, **_kwargs):
            return [_paper()]

        def close(self):
            pass

    class Verifier:
        def check_many(self, urls):
            return {url: self.check(url) for url in urls}

        def check(self, url):
            status = "verified_pdf" if url == PDF else "invalid"
            return AccessCheck(status, url, final_url=url,
                               evidence={"full_download": status == "verified_pdf", "page_count": 7})

        def close(self):
            pass

    verifier = Verifier()
    service = ResearchService(
        config=ResearchConfig(cache_ttl_hours=0),
        storage=ResearchStorage(tmp_path / "research.sqlite3"),
        clients={"openalex": Source()}, link_validator=verifier,
        publisher_resolver=OjsPublisherResolver(verifier, http_client=_client()),
    )
    result = service.search(queries=["LoRa telemetry"], technical_focus="lora", limit=5)
    assert result["returned"] == 1
    assert result["results"][0]["full_text_url"] == PDF
    assert result["review_returned"] == 0
