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
            "Use search_academic_papers UMA vez com request igual à mensagem original "
            "e limit igual à quantidade pedida; o plugin faz busca adaptativa. "
            "Não faça tool_search/tool_describe nem crie várias consultas por padrão. "
            "'Artigos' é genérico: não use document_type=articles a menos que o usuário "
            "diga somente artigos de periódico/conferência. TCC explícito usa "
            "document_type=bachelor_thesis e é estrito. "
            "Mantenha exclude_electric_vehicles=true, salvo pedido explícito do usuário por EV. "
            "Responda apenas com os resultados atuais da ferramenta e full_text_url "
            "dos registros access_status=verified_pdf. "
            "Nunca complete DOI, título, autores, PDF ou quantidade de resultados de memória. "
            "Se retornarem menos trabalhos, informe isso e as fontes indisponíveis."
        )
    }


__all__ = ["academic_request_context"]
