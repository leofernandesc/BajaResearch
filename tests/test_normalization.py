from models import (
    Paper,
    is_long_form_document,
    normalize_document_type,
    normalize_doi,
    normalize_title,
)


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
    assert "link_verification" not in paper.to_dict(compact=True)
    assert paper.to_dict(compact=False)["link_verification"]["url"] == "invalid"


def test_document_type_normalization_does_not_infer_tcc_from_title() -> None:
    assert normalize_document_type("bachelorThesis") == "bachelor_thesis"
    assert normalize_document_type("TCC") == "bachelor_thesis"
    assert (
        normalize_document_type("Trabalho de Conclusão de Curso (Graduação)")
        == "bachelor_thesis"
    )
    assert normalize_document_type("Dissertação") == "master_thesis"
    assert is_long_form_document("doctoral thesis") is True
    paper = Paper("", "A escrita do TCC e a formação universitária", document_type="article")
    assert paper.document_type == "journal_article"
    assert is_long_form_document(paper.document_type) is False


def test_only_verified_pdf_is_exposed_as_full_text() -> None:
    candidate = Paper(
        "paper:candidate",
        "Candidate only",
        open_access_url="https://publisher.example/article",
    )
    assert candidate.to_dict()["full_text_url"] is None

    verified = Paper(
        "paper:verified",
        "Verified PDF",
        full_text_url="https://repository.example/document.pdf",
        access_status="verified_pdf",
        access_evidence={"method": "streamed_get_pdf_magic"},
    )
    assert verified.to_dict()["full_text_url"].endswith("document.pdf")
