from models import Paper, deduplicate_papers


def test_deduplicates_by_doi_and_merges_complementary_metadata():
    openalex = Paper(
        "",
        "Tubular chassis fatigue analysis",
        authors=["Alice Researcher"],
        year=2022,
        abstract="A useful abstract.",
        openalex_id="W123",
        doi="10.1000/ABC",
        sources=["openalex"],
        source_scores={"openalex": 0.8},
    )
    semantic = Paper(
        "",
        "Tubular chassis fatigue analysis",
        authors=["Bob Researcher"],
        year=2022,
        semantic_scholar_id="S123",
        doi="https://doi.org/10.1000/abc",
        citation_count=41,
        sources=["semantic_scholar"],
        source_scores={"semantic_scholar": 0.5},
    )
    merged = deduplicate_papers([openalex, semantic])
    assert len(merged) == 1
    assert merged[0].doi == "10.1000/abc"
    assert merged[0].authors == ["Alice Researcher", "Bob Researcher"]
    assert merged[0].abstract == "A useful abstract."
    assert merged[0].semantic_scholar_id == "S123"
    assert merged[0].citation_count == 41
    assert set(merged[0].sources) == {"openalex", "semantic_scholar"}


def test_deduplicates_by_normalized_title_and_year_without_doi():
    first = Paper("", "Baja SAE suspension optimization", year=2021, sources=["openalex"])
    second = Paper("", "BAJA-SAE suspension optimization", year=2021, sources=["crossref"])
    merged = deduplicate_papers([first, second])
    assert len(merged) == 1
    assert set(merged[0].sources) == {"openalex", "crossref"}


def test_fuzzy_matching_is_conservative_and_does_not_merge_short_titles():
    close = Paper("", "Finite element analysis of a tubular off road vehicle chassis", year=2020)
    variant = Paper("", "Finite-element analysis of tubular off-road vehicle chassis", year=2020)
    unrelated = Paper("", "Chassis design", year=2020)
    merged = deduplicate_papers([close, variant, unrelated])
    assert len(merged) == 2


def test_repeated_normalized_object_without_year_is_not_duplicated():
    paper = Paper("", "Projeto de suspensão Baja SAE", sources=["oasisbr"])
    copies = [Paper.from_dict(paper.to_storage_dict()) for _ in range(3)]
    merged = deduplicate_papers(copies)
    assert len(merged) == 1


def test_same_title_merges_after_repository_fills_missing_year():
    repository = Paper(
        "",
        "Avaliação da rigidez torcional do chassi Baja SAE",
        year=None,
        oasisbr_id="repository-record",
        sources=["oasisbr"],
    )
    api_record = Paper(
        "",
        "Avaliação da rigidez torcional do chassi Baja SAE",
        year=2016,
        openalex_id="W2016",
        sources=["openalex"],
    )
    before_enrichment = deduplicate_papers([repository, api_record])
    assert len(before_enrichment) == 2
    before_enrichment[0].year = 2016
    after_enrichment = deduplicate_papers(before_enrichment)
    assert len(after_enrichment) == 1
    assert set(after_enrichment[0].sources) == {"oasisbr", "openalex"}
