from models import Paper
from ranking import (
    filter_relevant_papers,
    is_electric_vehicle_paper,
    rank_papers,
)


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
        "technical_relevance", "focus_relevance", "application_context", "source_relevance",
        "completeness", "multi_source", "citation_signal", "recency_signal",
        "long_form", "passes_technical_gate", "passes_context_gate",
    }


def test_education_retrieval_terms_cannot_fake_electronics_relevance():
    generic = Paper(
        "", "Undergraduate Research and Development Explores Energy Conservation",
        abstract="Experimental vehicle projects include Baja SAE and Formula SAE.",
        topics=["Electrical engineering", "Engineering education"],
    )
    queries = [
        "Baja SAE eletrônica",
        "eletrônica off-road vehicle undergraduate thesis institutional repository",
    ]
    kept, rejected = filter_relevant_papers([generic], "eletronica", queries)
    assert kept == []
    assert rejected["wrong_technical_focus"] == 1
    ranked = rank_papers([generic], queries, technical_focus="eletronica")
    assert ranked[0].score_details["focus_relevance"] == 0
    assert ranked[0].score_details["passes_technical_gate"] == 0


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
    assert ranked[0].score_details["long_form"] == 1.0
    assert ranked[0].score_details["passes_context_gate"] == 1.0
    assert ranked[1].score_details["passes_context_gate"] == 0.0


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
    assert ranked[0].score_details["application_context"] == 0.0
    assert ranked[0].score_details["passes_context_gate"] == 0.0


def test_portuguese_electric_vehicle_title_is_detected():
    paper = Paper(
        "",
        "Análise estrutural de veículo elétrico Baja SAE",
        abstract="Projeto de bateria e powertrain para mobilidade elétrica.",
        year=2023,
    )
    ranked = rank_papers([paper], ["electronics Baja SAE"], prefer_theses=True)
    assert ranked[0].score_details["technical_relevance"] >= 0.0
    assert is_electric_vehicle_paper(paper) is True


def test_indonesian_electric_vehicle_title_is_detected():
    paper = Paper("", "Analisa struktur sasis kendaraan mobil listrik Baja SAE")
    assert is_electric_vehicle_paper(paper) is True


def test_formula_sae_eletrico_reverse_order_is_detected():
    paper = Paper("", "Otimização da suspensão de um veículo Formula SAE elétrico")
    assert is_electric_vehicle_paper(paper) is True


def test_hard_gates_reject_wrong_focus_and_false_tcc_match():
    good = Paper(
        "",
        "Projeto de suspensão dianteira para um veículo mini Baja SAE",
        document_type="bachelorThesis",
    )
    aero = Paper(
        "",
        "Aerodynamic optimization of a Formula Student rear wing",
        document_type="article",
    )
    false_tcc = Paper(
        "",
        "A escrita do TCC e a formação universitária",
        document_type="article",
    )
    kept, rejected = filter_relevant_papers(
        [aero, false_tcc, good],
        "suspension optimization",
        ["Baja SAE suspension optimization"],
        require_context=True,
    )
    assert [paper.title for paper in kept] == [good.title]
    assert rejected["wrong_technical_focus"] == 2
    assert rejected["missing_baja_context"] == 0


def test_hard_gate_rejects_matching_technical_paper_without_vehicle_context():
    generic = Paper("", "Suspension optimization methods", document_type="article")
    kept, rejected = filter_relevant_papers(
        [generic],
        "suspension optimization",
        ["Baja SAE suspension optimization"],
        require_context=True,
    )
    assert kept == []
    assert rejected["missing_baja_context"] == 1


def test_english_focus_matches_portuguese_repository_terminology():
    paper = Paper(
        "",
        "Projeto de um sistema embarcado de aquisição de dados com implementação e testes de sistema de telemetria",
        document_type="TCC",
        topics=["Baja SAE"],
    )
    kept, rejected = filter_relevant_papers(
        [paper],
        "telemetry data acquisition sensors CAN",
        ["Baja SAE telemetry data acquisition"],
        require_context=True,
    )
    assert kept == [paper]
    assert rejected == {"wrong_technical_focus": 0, "missing_baja_context": 0}
