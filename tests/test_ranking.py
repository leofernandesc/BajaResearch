from models import Paper
from ranking import rank_papers


def test_direct_query_match_beats_high_citation_off_topic_paper():
    direct = Paper(
        "",
        "Baja SAE suspension optimization using vehicle dynamics",
        abstract="Suspension geometry and off-road vehicle performance.",
        year=2021,
        citation_count=15,
        sources=["openalex"],
        source_scores={"openalex": 0.7},
    )
    famous = Paper(
        "",
        "A general theory of wireless sensor networks",
        abstract="A broad communication survey.",
        year=2022,
        citation_count=100000,
        sources=["semantic_scholar"],
        source_scores={"semantic_scholar": 0.9},
    )
    ranked = rank_papers([famous, direct], ["Baja SAE suspension optimization"], current_year=2026)
    assert ranked[0].title == direct.title
    assert set(ranked[0].score_details) == {
        "query_relevance", "source_relevance", "multi_source",
        "citation_signal", "recency_signal", "context_signal",
        "thesis_signal", "context_gate",
    }


def test_multiple_sources_and_missing_abstract_are_supported():
    paper = Paper(
        "",
        "Formula Student tubular chassis",
        year=2010,
        sources=["openalex", "crossref"],
        source_scores={"openalex": 0.6, "crossref": 0.35},
    )
    result = rank_papers([paper], ["tubular chassis"], current_year=2026)
    assert result[0].ranking_score is not None
    assert result[0].score_details["multi_source"] == 0.5


def test_thesis_preference_and_context_gate_are_visible_in_score_details():
    thesis = Paper(
        "",
        "Electronic telemetry system for Baja SAE vehicle: undergraduate thesis",
        abstract="Data acquisition and CAN telemetry for an off-road vehicle.",
        venue="Institutional Repository",
        document_type="dissertation",
        year=2022,
        sources=["openalex"],
        source_scores={"openalex": 0.5},
    )
    generic = Paper(
        "",
        "Electronic telemetry system design",
        abstract="A general electronics design study.",
        year=2024,
        citation_count=500,
        sources=["crossref"],
        source_scores={"crossref": 0.35},
    )
    ranked = rank_papers(
        [generic, thesis],
        ["electronics Baja SAE vehicle"],
        current_year=2026,
        prefer_theses=True,
        require_context=True,
    )
    assert ranked[0].title == thesis.title
    assert ranked[0].score_details["thesis_signal"] == 1.0
    assert ranked[0].score_details["context_gate"] == 1.0
    assert ranked[1].score_details["context_gate"] == 0.55


def test_spanish_baja_word_does_not_fake_baja_sae_context():
    paper = Paper(
        "",
        "Advanced wireless power transfer technologies",
        abstract="La propuesta mejora la baja eficiencia del rectificador.",
        document_type="dissertation",
        year=2021,
        sources=["openalex"],
    )
    ranked = rank_papers(
        [paper],
        ["electronics Baja SAE"],
        current_year=2026,
        prefer_theses=True,
        require_context=True,
    )
    assert ranked[0].score_details["context_signal"] == 0.0
    assert ranked[0].score_details["context_gate"] == 0.55
