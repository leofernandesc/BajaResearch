"""Small deterministic retrieval guards around LLM-created queries."""

from __future__ import annotations

try:
    from .models import normalize_title
except ImportError:  # pragma: no cover - direct test imports
    from models import normalize_title


_BAJA_CONTEXT_MARKERS = (
    "baja",
    "formula sae",
    "formula student",
    "off road",
    "off-road",
    "offroad",
    "atv",
    "automotive",
    "vehicle",
    "motorsport",
    "telemetry",
    "can bus",
)
_THESIS_QUERY_MARKERS = (
    "thesis",
    "dissertation",
    "tcc",
    "monograph",
    "monografia",
    "undergraduate thesis",
    "master thesis",
    "doctoral thesis",
    "institutional repository",
    "repository",
    "repositorio",
)

# Retrieval vocabulary, not a subject whitelist. Unknown engineering topics
# retain their own words and remain searchable.
_BILINGUAL_TERMS = {
    "suspension": ("suspension", "suspensão"),
    "suspensao": ("suspension", "suspensão"),
    "chassis": ("chassis", "chassi"),
    "chassi": ("chassis", "chassi"),
    "electronics": ("electronics", "eletrônica"),
    "eletronica": ("electronics", "eletrônica"),
    "telemetry": ("telemetry", "telemetria"),
    "telemetria": ("telemetry", "telemetria"),
    "brakes": ("brakes", "freios"),
    "brake": ("brakes", "freios"),
    "freios": ("brakes", "freios"),
    "freio": ("brakes", "freios"),
    "steering": ("steering", "direção"),
    "direcao": ("steering", "direção"),
    "cvt": ("CVT", "CVT"),
    "powertrain": ("powertrain", "trem de força"),
    "transmission": ("transmission", "transmissão"),
    "transmissao": ("transmission", "transmissão"),
    "ergonomics": ("ergonomics", "ergonomia"),
    "ergonomia": ("ergonomics", "ergonomia"),
    "manufacturing": ("manufacturing", "manufatura"),
    "manufatura": ("manufacturing", "manufatura"),
    "welding": ("welding", "soldagem"),
    "soldagem": ("welding", "soldagem"),
}
_IGNORED_FOCUS_WORDS = {
    "a", "as", "o", "os", "de", "da", "do", "das", "dos", "em", "para",
    "sobre", "com", "e", "the", "of", "for", "in", "on", "and", "about",
    "busca", "buscar", "busque", "encontre", "encontrar", "find", "search",
    "artigo", "artigos", "paper", "papers", "trabalho", "trabalhos", "academico",
    "academicos", "referencia", "referencias", "tcc", "tccs", "monografia",
    "monografias", "dissertacao", "dissertacoes", "tese", "teses", "thesis",
    "dissertation", "pdf", "gratuito", "gratuitos", "gratis", "completo",
    "completos", "verificado", "verificados", "baja", "sae", "mini",
    "formula", "student", "off", "road", "vehicle", "vehicles", "atv",
    "otimizacao", "optimization", "optimisation", "projeto", "design",
    "analise", "analysis", "estudo", "study", "modelagem", "modeling",
    "simulation", "simulacao", "repository", "repositorio", "institutional",
}


def infer_technical_focus(value: str) -> str:
    """Extract a compact topic when Hermes passes only a natural-language query."""
    words = [
        word for word in normalize_title(value).split()
        if len(word) > 1 and word not in _IGNORED_FOCUS_WORDS
    ]
    if not words:
        return ""
    for word in words:
        if word in _BILINGUAL_TERMS:
            return word
    return " ".join(words[:2])


def infer_document_type(value: str) -> str:
    """Infer strict work type only from an explicit user type word."""
    intent = normalize_title(value)
    words = set(intent.split())
    if words & {"tcc", "tccs"} or "trabalho de conclusao" in intent:
        return "bachelor_thesis"
    if words & {"artigo", "artigos", "article", "articles"}:
        return "articles"
    return "any"


def expand_plugin_queries(
    queries: list[str],
    *,
    baja_context: bool,
    prefer_theses: bool,
    technical_focus: str | None = None,
) -> list[str]:
    """Add narrow safety variants without replacing Hermes query expansion."""
    expanded = list(queries)
    joined = " ".join(expanded).casefold()
    base = expanded[0]
    has_context = any(marker in joined for marker in _BAJA_CONTEXT_MARKERS)
    core = infer_technical_focus(technical_focus or base)
    if baja_context and core:
        english, portuguese = _BILINGUAL_TERMS.get(core, (core, core))
        # Compact context-bearing queries are independent of whether the LLM
        # added quotes, access words or too many methodology terms.
        expanded = [f"Baja SAE {portuguese}", f"Baja SAE {english}", *expanded]
    if baja_context and not has_context:
        expanded.extend(
            (f"Baja SAE {base}", f"{base} off-road vehicle Formula SAE")
        )
    joined = " ".join(expanded).casefold()
    if prefer_theses and not any(marker in joined for marker in _THESIS_QUERY_MARKERS):
        expanded.extend(
            (
                f"{base} Baja SAE thesis dissertation",
                f"{base} off-road vehicle undergraduate thesis institutional repository",
            )
        )
    return list(dict.fromkeys(expanded))[:8]


__all__ = ["expand_plugin_queries", "infer_document_type", "infer_technical_focus"]
