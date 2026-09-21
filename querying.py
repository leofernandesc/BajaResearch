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
    queries: list[str], *, baja_context: bool, prefer_theses: bool
) -> list[str]:
    """Add narrow safety variants without replacing Hermes query expansion."""
    expanded = list(queries)
    joined = " ".join(expanded).casefold()
    base = expanded[0]
    if baja_context and not any(marker in joined for marker in _BAJA_CONTEXT_MARKERS):
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
