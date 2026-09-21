"""Runtime configuration for the standalone Hermes plugin."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any


@dataclass(frozen=True)
class ResearchConfig:
    cache_ttl_hours: float = 24.0
    request_timeout_seconds: float = 15.0
    max_retries: int = 2
    openalex_api_key: str | None = None
    semantic_scholar_api_key: str | None = None
    crossref_mailto: str | None = None
    validate_links: bool = True
    link_timeout_seconds: float = 6.0


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, min(4, int(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _context_setting(ctx: Any, name: str, default: Any) -> Any:
    try:
        value = ctx.get_config(name, default=default)
        return default if value is None else value
    except Exception:
        return default


def config_from_context(ctx: Any) -> ResearchConfig:
    """Combine public Hermes plugin settings with environment credentials."""
    context_ttl = _context_setting(ctx, "cache_ttl_hours", 24)
    context_timeout = _context_setting(ctx, "request_timeout_seconds", 15)
    try:
        context_ttl = float(context_ttl)
    except (TypeError, ValueError):
        context_ttl = 24.0
    try:
        context_timeout = float(context_timeout)
    except (TypeError, ValueError):
        context_timeout = 15.0
    return ResearchConfig(
        cache_ttl_hours=_env_float("BAJA_RESEARCH_CACHE_TTL_HOURS", context_ttl),
        request_timeout_seconds=_env_float(
            "BAJA_RESEARCH_REQUEST_TIMEOUT_SECONDS", context_timeout
        ),
        max_retries=_env_int("BAJA_RESEARCH_MAX_RETRIES", 2),
        openalex_api_key=os.getenv("OPENALEX_API_KEY") or None,
        semantic_scholar_api_key=os.getenv("SEMANTIC_SCHOLAR_API_KEY") or None,
        crossref_mailto=os.getenv("CROSSREF_MAILTO") or None,
        validate_links=_env_bool("BAJA_RESEARCH_VALIDATE_LINKS", True),
        link_timeout_seconds=_env_float(
            "BAJA_RESEARCH_LINK_TIMEOUT_SECONDS", 6.0
        ),
    )


__all__ = ["ResearchConfig", "config_from_context"]
