"""Failures reproduced from short, real-world Baja Research requests."""

from clients.link_validator import AccessCheck
from models import Paper
from querying import infer_document_type
from storage import ResearchStorage
from tools import ResearchConfig, ResearchService


class Source:
    configured = False

    def __init__(self, papers=(), error=None):
        self.papers = list(papers)
        self.error = error
        self.queries = []

    def search(self, query, **_kwargs):
        self.queries.append(query)
        if self.error:
            raise self.error
        return list(self.papers)

    def close(self):
        pass


class PdfVerifier:
    def check_many(self, urls):
        return {
            url: AccessCheck(
                "verified_pdf", url, final_url=url,
                evidence={"pdf_magic": True, "full_download": True, "page_count": 35},
            )
            for url in urls
        }

    def check(self, url):
        return self.check_many([url])[url]

    def close(self):
        pass


def test_article_word_means_academic_works_not_strict_article_type():
    assert infer_document_type("artigos sobre LoRa") == "any"
    assert infer_document_type("somente artigos de periódico sobre LoRa") == "articles"
    assert infer_document_type("3 TCCs sobre suspensão") == "bachelor_thesis"


def test_short_lora_request_retries_after_irrelevant_raw_results(tmp_path):
    irrelevant = [
        Paper("", f"LoRa precision agriculture experiment {index}",
              document_type="article", sources=["openalex"],
              open_access_url=f"https://example.org/irrelevant-{index}.pdf")
        for index in range(25)
    ]
    relevant = Paper(
        "", "A comparative study between LoRa and Zigbee transmission for racing car telemetry",
        document_type="conference_paper", sources=["openalex"],
        open_access_url="https://example.org/racing-telemetry.pdf",
    )

    class OpenAlex(Source):
        def search(self, query, **kwargs):
            self.queries.append(query)
            return [relevant] if query.startswith("Formula SAE") else irrelevant

    openalex = OpenAlex()
    service = ResearchService(
        config=ResearchConfig(cache_ttl_hours=0),
        storage=ResearchStorage(tmp_path / "research.sqlite3"),
        clients={"openalex": openalex},
        link_validator=PdfVerifier(),
    )
    result = service.search(queries=["artigos sobre LoRa"], limit=3,
                            original_query="artigos sobre LoRa")
    assert result["returned"] >= 1
    assert relevant.title in [item["title"] for item in result["results"]]
    assert len(openalex.queries) >= 2


def test_tcc_is_preferred_but_good_article_not_discarded(tmp_path):
    tcc = Paper("", "Telemetria LoRa em um protótipo Baja SAE",
                document_type="bachelor_thesis", sources=["oasisbr"],
                open_access_url="https://example.org/tcc.pdf")
    article = Paper("", "LoRa telemetry for Formula SAE racing vehicles",
                    document_type="conference_paper", sources=["openalex"],
                    open_access_url="https://example.org/article.pdf")
    service = ResearchService(
        config=ResearchConfig(cache_ttl_hours=0),
        storage=ResearchStorage(tmp_path / "research.sqlite3"),
        clients={"oasisbr": Source([tcc]), "openalex": Source([article])},
        link_validator=PdfVerifier(),
    )
    result = service.search(queries=["artigos sobre LoRa"], limit=3,
                            original_query="artigos sobre LoRa")
    assert [item["title"] for item in result["results"]][:2] == [tcc.title, article.title]
