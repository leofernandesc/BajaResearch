"""Confirmed Baja works and transferable literature must never be conflated."""

from clients.link_validator import AccessCheck
from models import Paper
from schemas import validate_search_args
from storage import ResearchStorage
from tools import ResearchConfig, ResearchService


class Source:
    configured = False

    def __init__(self, papers):
        self.papers = papers

    def search(self, _query, **_kwargs):
        return list(self.papers)

    def close(self):
        pass


class Verifier:
    def __init__(self, invalid=()):
        self.invalid = set(invalid)

    def check_many(self, urls):
        return {url: self.check(url) for url in urls}

    def check(self, url):
        if url in self.invalid:
            return AccessCheck("invalid", url, reason="not_pdf")
        return AccessCheck("verified_pdf", url, final_url=url,
                           evidence={"full_download": True, "page_count": 20})

    def close(self):
        pass


def test_short_request_targets_at_least_five_confirmed_works():
    assert validate_search_args({"request": "3 artigos sobre LoRa", "limit": 3})["limit"] == 5
    assert validate_search_args({"request": "8 artigos sobre LoRa", "limit": 8})["limit"] == 8


def test_lora_in_abstract_is_sufficient_for_baja_tcc(tmp_path):
    tcc = Paper("", "Sistema embarcado de telemetria para Baja SAE",
                abstract="Testes com modulo LoRa para comunicacao veicular.",
                document_type="bachelor_thesis", sources=["ufscar"],
                open_access_url="https://example.org/tcc.pdf")
    service = ResearchService(
        config=ResearchConfig(cache_ttl_hours=0),
        storage=ResearchStorage(tmp_path / "research.sqlite3"),
        clients={"ufscar": Source([tcc])}, link_validator=Verifier(),
    )
    result = service.search(queries=["artigos sobre LoRa"], original_query="artigos sobre LoRa", limit=5)
    assert [paper["title"] for paper in result["results"]] == [tcc.title]


def test_review_candidates_are_separate_verified_and_cached(tmp_path):
    approved = [Paper("", f"LoRa telemetry Baja SAE vehicle {index}",
                      document_type="bachelor_thesis", sources=["openalex"],
                      open_access_url=f"https://example.org/baja-{index}.pdf")
                for index in range(5)]
    transferable = [Paper("", f"LoRa wireless network agriculture study {index}",
                          document_type="article", sources=["openalex"],
                          open_access_url=f"https://example.org/transfer-{index}.pdf")
                    for index in range(7)]
    blocked = Paper("", "LoRa networks for urban telemetry", sources=["openalex"],
                    open_access_url="https://example.org/blocked.pdf")
    verifier = Verifier({blocked.open_access_url})
    service = ResearchService(
        config=ResearchConfig(cache_ttl_hours=24),
        storage=ResearchStorage(tmp_path / "research.sqlite3"),
        clients={"openalex": Source([*approved, *transferable, blocked])},
        link_validator=verifier,
    )
    first = service.search(queries=["artigos sobre LoRa"], original_query="artigos sobre LoRa", limit=5)
    assert first["returned"] == 5
    assert first["review_returned"] == 5
    assert all(p["access_status"] == "verified_pdf" for p in first["review_candidates"])
    assert all(p["review_reason"] == "indirect_baja_application" for p in first["review_candidates"])
    assert not {p["internal_id"] for p in first["results"]} & {p["internal_id"] for p in first["review_candidates"]}
    second = service.search(queries=["artigos sobre LoRa"], original_query="artigos sobre LoRa", limit=5)
    assert second["cache"]["hit"] is True
    assert second["review_returned"] == 5
    verifier.invalid.add(second["review_candidates"][0]["full_text_url"])
    third = service.search(queries=["artigos sobre LoRa"], original_query="artigos sobre LoRa", limit=5)
    assert third["cache"]["hit"] is True
    assert third["review_returned"] == 4


def test_abstract_only_lora_in_generic_iot_is_not_a_review_candidate(tmp_path):
    generic = Paper("", "BIM and IoT Integration for Construction Management",
                    abstract="LoRa is one communication example.",
                    sources=["openalex"], open_access_url="https://example.org/iot.pdf")
    service = ResearchService(
        config=ResearchConfig(cache_ttl_hours=0),
        storage=ResearchStorage(tmp_path / "research.sqlite3"),
        clients={"openalex": Source([generic])}, link_validator=Verifier(),
    )
    result = service.search(queries=["artigos sobre LoRa"], original_query="artigos sobre LoRa")
    assert result["review_returned"] == 0


def test_direct_formula_work_precedes_indirect_offroad_article(tmp_path):
    indirect = Paper("", "LoRa navigation on an off-road mountain vehicle",
                     document_type="journal_article", ranking_score=0.9,
                     open_access_url="https://example.org/mountain.pdf")
    direct = Paper("", "Student Formula Cars and All-terrain vehicles telemetry",
                   abstract="The telemetry uses LoRa transceivers.",
                   document_type="journal_article", ranking_score=0.5,
                   open_access_url="https://example.org/formula.pdf")
    service = ResearchService(
        storage=ResearchStorage(tmp_path / "research.sqlite3"),
        clients={"openalex": Source([])}, link_validator=Verifier(),
    )
    selected, _ = service._select_final_papers(
        [indirect, direct], limit=1, open_access_only=True,
        prefer_long_form=True,
    )
    assert [paper.title for paper in selected] == [direct.title]
