"""Hermes tool schemas and lightweight argument validation."""

from __future__ import annotations

from typing import Any, Mapping

try:
    from .models import normalize_doi, normalize_title
    from .querying import infer_document_type, infer_technical_focus
except ImportError:  # pragma: no cover - direct test imports
    from models import normalize_doi, normalize_title
    from querying import infer_document_type, infer_technical_focus


SEARCH_SCHEMA = {
    "name": "search_academic_papers",
    "description": "Search real Baja/Formula/off-road academic literature across open repositories and academic APIs; return only anonymously verified full-text PDFs.",
    "parameters": {
        "type": "object",
        "properties": {
            "queries": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 8,
                "description": "One to eight complementary academic queries, preferably in technical English.",
            },
            "technical_focus": {
                "type": "string",
                "minLength": 2,
                "description": "Optional technical focus. Inferred from queries when omitted; Baja SAE and verified free PDF are always implicit.",
            },
            "document_type": {
                "type": "string",
                "enum": ["any", "bachelor_thesis", "long_form", "articles"],
                "description": "Optional strict type filter. TCC requests imply bachelor_thesis; article requests imply articles. No filter otherwise.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 5,
                "description": "Final number of papers to return.",
            },
            "year_from": {"type": ["integer", "null"], "minimum": 1000, "maximum": 2200},
            "year_to": {"type": ["integer", "null"], "minimum": 1000, "maximum": 2200},
            "exclude_electric_vehicles": {
                "type": "boolean",
                "default": True,
                "description": "Exclude papers primarily about electric, hybrid or fuel-cell vehicles unless explicitly disabled.",
            },
            "document_preference": {
                "type": "string",
                "enum": ["long_form_first", "articles_first"],
                "default": "long_form_first",
                "description": "Put TCCs, dissertations, theses and monographs first by default; use articles_first only when explicitly requested.",
            },
            "original_query": {"type": ["string", "null"], "description": "The user's original question, when useful for diagnostics."},
            "refresh_cache": {"type": "boolean", "default": False, "description": "Ignore a fresh identical search cache entry."},
        },
        "required": ["queries"],
        "additionalProperties": False,
    },
}

GET_PAPER_SCHEMA = {
    "name": "get_paper",
    "description": "Retrieve consolidated bibliographic metadata for a DOI, OpenAlex ID, Semantic Scholar ID, or internal BAJA Research ID.",
    "parameters": {
        "type": "object",
        "properties": {
            "identifier": {"type": "string", "minLength": 1},
        },
        "required": ["identifier"],
        "additionalProperties": False,
    },
}

RELATED_SCHEMA = {
    "name": "find_related_papers",
    "description": "Find related Baja/Formula/off-road work and return only anonymously verified free full-text PDFs.",
    "parameters": {
        "type": "object",
        "properties": {
            "identifier": {"type": "string", "minLength": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
        },
        "required": ["identifier"],
        "additionalProperties": False,
    },
}

CITATION_SCHEMA = {
    "name": "format_citation",
    "description": "Format metadata returned by academic sources as ABNT or BibTeX without inventing absent fields.",
    "parameters": {
        "type": "object",
        "properties": {
            "identifier": {"type": "string", "minLength": 1},
            "style": {"type": "string", "enum": ["abnt", "bibtex"], "default": "abnt"},
        },
        "required": ["identifier"],
        "additionalProperties": False,
    },
}

STATS_SCHEMA = {
    "name": "research_cache_stats",
    "description": "Show BAJA Research SQLite cache counts, configured sources, and last observed API statuses.",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}


def _year(value: Any, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer year")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer year") from exc
    if not 1000 <= parsed <= 2200:
        raise ValueError(f"{name} must be between 1000 and 2200")
    return parsed


def validate_search_args(args: Mapping[str, Any]) -> dict[str, Any]:
    raw_queries = args.get("queries")
    if not isinstance(raw_queries, list):
        raise ValueError("queries must be a non-empty list of strings")
    queries = [str(query).strip() for query in raw_queries if isinstance(query, str) and query.strip()]
    if not queries:
        raise ValueError("queries must contain at least one non-empty string")
    if len(queries) > 8:
        raise ValueError("queries accepts at most 8 items")
    technical_focus = args.get("technical_focus")
    if technical_focus is None:
        technical_focus = infer_technical_focus(
            str(args.get("original_query") or " ".join(queries))
        )
    if not isinstance(technical_focus, str) or len(technical_focus.strip()) < 2:
        raise ValueError("technical_focus must be a non-empty technical description")
    limit = args.get("limit", 5)
    if isinstance(limit, bool):
        raise ValueError("limit must be an integer")
    try:
        limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    if not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20")
    year_from = _year(args.get("year_from"), "year_from")
    year_to = _year(args.get("year_to"), "year_to")
    if year_from and year_to and year_from > year_to:
        raise ValueError("year_from cannot be greater than year_to")
    exclude_electric_vehicles = args.get("exclude_electric_vehicles", True)
    if not isinstance(exclude_electric_vehicles, bool):
        raise ValueError("exclude_electric_vehicles must be boolean")
    refresh_cache = args.get("refresh_cache", False)
    if not isinstance(refresh_cache, bool):
        raise ValueError("refresh_cache must be boolean")
    document_preference = str(
        args.get("document_preference", "long_form_first")
    ).strip()
    if document_preference not in {"long_form_first", "articles_first"}:
        raise ValueError(
            "document_preference must be 'long_form_first' or 'articles_first'"
        )
    original_query = args.get("original_query")
    if original_query is not None and not isinstance(original_query, str):
        raise ValueError("original_query must be a string when supplied")
    document_type = args.get("document_type")
    if document_type is None:
        document_type = infer_document_type(" ".join([original_query or "", *queries]))
    if document_type not in {"any", "bachelor_thesis", "long_form", "articles"}:
        raise ValueError("document_type must be any, bachelor_thesis, long_form or articles")
    return {
        "queries": queries,
        "technical_focus": technical_focus.strip(),
        "document_type": document_type,
        "limit": limit,
        "year_from": year_from,
        "year_to": year_to,
        "exclude_electric_vehicles": exclude_electric_vehicles,
        "document_preference": document_preference,
        "original_query": original_query.strip() if original_query else None,
        "refresh_cache": refresh_cache,
    }


def validate_identifier_args(args: Mapping[str, Any]) -> str:
    identifier = args.get("identifier")
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError("identifier must be a non-empty string")
    return identifier.strip()


def validate_limit(args: Mapping[str, Any], default: int = 5) -> int:
    value = args.get("limit", default)
    if isinstance(value, bool):
        raise ValueError("limit must be an integer")
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    if not 1 <= value <= 20:
        raise ValueError("limit must be between 1 and 20")
    return value


def validate_style(args: Mapping[str, Any]) -> str:
    style = str(args.get("style", "abnt")).strip().lower()
    if style not in {"abnt", "bibtex"}:
        raise ValueError("style must be 'abnt' or 'bibtex'")
    return style


__all__ = [
    "CITATION_SCHEMA",
    "GET_PAPER_SCHEMA",
    "RELATED_SCHEMA",
    "SEARCH_SCHEMA",
    "STATS_SCHEMA",
    "normalize_doi",
    "normalize_title",
    "validate_identifier_args",
    "validate_limit",
    "validate_search_args",
    "validate_style",
]
