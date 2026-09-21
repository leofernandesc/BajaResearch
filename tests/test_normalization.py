from models import Paper, normalize_doi, normalize_title


def test_normalize_doi_accepts_common_forms():
    expected = "10.1234/example.1"
    values = [
        expected,
        "https://doi.org/10.1234/EXAMPLE.1",
        "http://dx.doi.org/10.1234/example.1.",
        "doi:10.1234/example.1",
        "(10.1234/example.1)",
    ]
    assert [normalize_doi(value) for value in values] == [expected] * len(values)


def test_normalize_doi_rejects_non_doi_values():
    assert normalize_doi("") is None
    assert normalize_doi("https://example.com/paper") is None
    assert normalize_doi("10/too-short") is None


def test_normalize_title_is_accent_and_punctuation_insensitive():
    assert normalize_title("Análise de Fadiga — Chassi Baja SAE!") == "analise de fadiga chassi baja sae"


def test_paper_serialization_is_compact_without_raw_payload():
    paper = Paper(
        internal_id="paper:test",
        title="A title",
        abstract="A" * 1000,
        metadata={"selected": "value"},
    )
    compact = paper.to_dict(compact=True)
    assert "abstract" not in compact
    assert len(compact["abstract_snippet"]) == 700
    assert "metadata" not in compact
    assert paper.to_dict(compact=False)["metadata"] == {"selected": "value"}


def test_invalid_public_link_is_not_exposed():
    paper = Paper(
        internal_id="paper:bad-link",
        title="A paper with a stale repository URL",
        url="https://example.invalid/stale.pdf",
        link_status={"url": "invalid"},
    )
    assert paper.to_dict(compact=True)["url"] is None
    assert paper.to_dict(compact=True)["link_verification"]["url"] == "invalid"
