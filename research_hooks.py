"""Turn-scoped guidance for short academic requests in chat and WhatsApp."""

from __future__ import annotations

import re
from typing import Any


_ACADEMIC_REQUEST = re.compile(
    r"\b(?:tccs?|artigos?|papers?|literatura|refer[eê]ncias?|"
    r"disserta(?:ç|c)(?:ão|ao|ões|oes)|teses?|monografias?|"
    r"theses?|dissertations?|academic\s+works?)\b",
    re.IGNORECASE,
)


def academic_request_context(user_message: str = "", platform: str = "", **_kwargs: Any) -> dict[str, str] | None:
    """Inject no facts; just make the plugin's fixed policy visible to Hermes."""
    if not isinstance(user_message, str) or not _ACADEMIC_REQUEST.search(user_message):
        return None
    return {
        "context": (
            "BAJA Research: neste pedido acadêmico, Baja SAE/Formula/off-road é contexto "
            "implícito, e o PDF completo, gratuito e verificado já é obrigatório. "
            "Use search_academic_papers primeiro, em UMA chamada com o assunto do usuário "
            "e o limite pedido; consultas curtas em português são válidas, e technical_focus é opcional. "
            "Se pediu TCC, use document_type=bachelor_thesis; se pediu artigos, "
            "document_type=articles. Não repita busca por padrão; só amplie uma vez se "
            "vier zero e restar assunto técnico específico. "
            "Mantenha exclude_electric_vehicles=true, salvo pedido explícito do usuário por EV. "
            "Responda apenas com os resultados atuais da ferramenta e full_text_url "
            "dos registros access_status=verified_pdf. "
            "Nunca complete DOI, título, autores, PDF ou quantidade de resultados de memória. "
            "Se retornarem menos trabalhos, informe isso e as fontes indisponíveis."
        )
    }


__all__ = ["academic_request_context"]
