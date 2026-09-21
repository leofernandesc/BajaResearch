"""Small deterministic retrieval guards around LLM-created queries."""

from __future__ import annotations


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
    core = (technical_focus or base).strip()
    if baja_context and core:
        contextual_core = f"Baja SAE {core}"
        if contextual_core.casefold() not in {query.casefold() for query in expanded}:
            # A concise contextual query is a safety net when the LLM supplied
            # only over-constrained thesis/repository variants. It also gives
            # repositories a realistic chance to return long-form records.
            expanded.insert(0, contextual_core)
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


__all__ = ["expand_plugin_queries"]
