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
