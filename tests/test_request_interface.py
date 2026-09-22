"""One short WhatsApp message must be enough to start a safe search."""

from schemas import validate_search_args


def test_short_request_infers_topic_and_keeps_tcc_preference():
    parsed = validate_search_args({"request": "artigos sobre LoRa", "limit": 3})
    assert parsed["queries"] == ["artigos sobre LoRa"]
    assert parsed["technical_focus"] == "lora"
    assert parsed["document_type"] == "any"
    assert parsed["original_query"] == "artigos sobre LoRa"


def test_request_count_needs_no_extra_field():
    assert validate_search_args({"request": "busque 3 artigos sobre LoRa"})["limit"] == 3
    assert validate_search_args({"request": "LoRa 868 MHz"})["limit"] == 5


def test_llm_cannot_make_generic_articles_strict():
    parsed = validate_search_args({"request": "artigos sobre LoRa", "document_type": "articles"})
    assert parsed["document_type"] == "any"


def test_explicit_tcc_overrides_llm_type_guess():
    parsed = validate_search_args({"request": "3 TCCs sobre suspensão", "document_type": "any"})
    assert parsed["document_type"] == "bachelor_thesis"


def test_legacy_queries_still_work():
    parsed = validate_search_args({"queries": ["Formula SAE suspension"]})
    assert parsed["queries"] == ["Formula SAE suspension"]
