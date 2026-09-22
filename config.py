"""Runtime configuration for the standalone Hermes plugin."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any


@dataclass(frozen=True)
class ResearchConfig:
    cache_ttl_hours: float = 24.0
    source_cache_ttl_hours: float = 24.0
    request_timeout_seconds: float = 8.0
    global_timeout_seconds: float = 25.0
    circuit_breaker_seconds: float = 60.0
    max_retries: int = 1
    openalex_api_key: str | None = None
    semantic_scholar_api_key: str | None = None
    crossref_mailto: str | None = None
    unpaywall_email: str | None = None
    access_timeout_seconds: float = 5.0
    access_valid_ttl_hours: float = 1.0
    access_invalid_ttl_hours: float = 24.0
    access_temporary_ttl_hours: float = 1.0


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


def _context_setting(ctx: Any, name: str, default: Any) -> Any:
    try:
        value = ctx.get_config(name, default=default)
        return default if value is None else value
    except Exception:
        return default


def config_from_context(ctx: Any) -> ResearchConfig:
    """Combine public Hermes plugin settings with environment credentials."""
    context_ttl = _context_setting(ctx, "cache_ttl_hours", 24)
    context_timeout = _context_setting(ctx, "request_timeout_seconds", 8)
    try:
        context_ttl = float(context_ttl)
    except (TypeError, ValueError):
        context_ttl = 24.0
    try:
        context_timeout = float(context_timeout)
    except (TypeError, ValueError):
        context_timeout = 8.0
    return ResearchConfig(
        cache_ttl_hours=_env_float("BAJA_RESEARCH_CACHE_TTL_HOURS", context_ttl),
        source_cache_ttl_hours=_env_float(
            "BAJA_RESEARCH_SOURCE_CACHE_TTL_HOURS", 24.0
        ),
        request_timeout_seconds=_env_float(
            "BAJA_RESEARCH_REQUEST_TIMEOUT_SECONDS", context_timeout
        ),
        global_timeout_seconds=_env_float(
            "BAJA_RESEARCH_GLOBAL_TIMEOUT_SECONDS", 25.0
        ),
        circuit_breaker_seconds=_env_float(
            "BAJA_RESEARCH_CIRCUIT_BREAKER_SECONDS", 60.0
        ),
        max_retries=_env_int("BAJA_RESEARCH_MAX_RETRIES", 1),
        openalex_api_key=os.getenv("OPENALEX_API_KEY") or None,
        semantic_scholar_api_key=os.getenv("SEMANTIC_SCHOLAR_API_KEY") or None,
        crossref_mailto=os.getenv("CROSSREF_MAILTO") or None,
        unpaywall_email=os.getenv("UNPAYWALL_EMAIL") or None,
        access_timeout_seconds=_env_float(
            "BAJA_RESEARCH_ACCESS_TIMEOUT_SECONDS", 5.0
        ),
        access_valid_ttl_hours=_env_float(
            "BAJA_RESEARCH_ACCESS_VALID_TTL_HOURS", 1.0
        ),
        access_invalid_ttl_hours=_env_float(
            "BAJA_RESEARCH_ACCESS_INVALID_TTL_HOURS", 24.0
        ),
        access_temporary_ttl_hours=_env_float(
            "BAJA_RESEARCH_ACCESS_TEMPORARY_TTL_HOURS", 1.0
        ),
    )


__all__ = ["ResearchConfig", "config_from_context"]
