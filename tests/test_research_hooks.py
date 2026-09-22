from research_hooks import academic_request_context


def test_short_tcc_request_gets_implicit_baja_pdf_policy():
    result = academic_request_context("Busque 3 TCCs sobre suspensão", platform="whatsapp")
    assert result is not None
    assert "Baja SAE" in result["context"]
    assert "PDF completo, gratuito e verificado" in result["context"]
    assert "document_type=bachelor_thesis" in result["context"]
    assert "exclude_electric_vehicles=true" in result["context"]


def test_nonacademic_request_is_untouched():
    assert academic_request_context("Como está a suspensão do protótipo?") is None
