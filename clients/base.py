"""Shared source-client contracts and small payload helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping

try:  # Works both as a Hermes-namespaced package and as a test package.
    from ..models import Paper
except ImportError:  # pragma: no cover - exercised by direct test imports
    from models import Paper


class SourceError(RuntimeError):
    """Structured failure from one academic source."""

    def __init__(
        self,
        source: str,
        message: str,
        *,
        status: int | None = None,
        code: str = "source_error",
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.source = source
        self.message = message
        self.status = status
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "source": self.source,
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.status is not None:
            result["status"] = self.status
        if self.retry_after is not None:
            result["retry_after_seconds"] = round(self.retry_after, 2)
        return result


@dataclass
class SourceResult:
    """Results from one source or one source/query operation."""

    source: str
    papers: list[Paper] = field(default_factory=list)
    latency_ms: float = 0.0
    error: dict[str, Any] | None = None
    query: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def timed_call(source: str, operation, *, query: str | None = None) -> SourceResult:
    """Run a source operation and turn failures into a structured result."""
    started = time.perf_counter()
    try:
        papers = list(operation())
        return SourceResult(
            source=source,
            papers=papers,
            latency_ms=(time.perf_counter() - started) * 1000,
            query=query,
        )
    except SourceError as exc:
        return SourceResult(
            source=source,
            latency_ms=(time.perf_counter() - started) * 1000,
            error=exc.to_dict(),
            query=query,
        )
    except Exception as exc:  # defensive boundary: one source must not abort all
        return SourceResult(
            source=source,
            latency_ms=(time.perf_counter() - started) * 1000,
            error={
                "source": source,
                "code": "unexpected_source_error",
                "message": f"{type(exc).__name__}: {exc}",
                "retryable": False,
            },
            query=query,
        )


def as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def year_from_payload(value: Any) -> int | None:
    if isinstance(value, Mapping):
        parts = value.get("date-parts") or value.get("date_parts")
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            value = parts[0][0]
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if 1000 <= result <= 2200 else None


def first_text(values: Any) -> str | None:
    for value in as_list(values):
        candidate = text(value)
        if candidate:
            return candidate
    return None


def abstract_from_inverted_index(value: Any) -> str | None:
    """Reconstruct OpenAlex's ``{word: [positions...]}`` abstract format."""
    if not isinstance(value, Mapping):
        return None
    words: dict[int, str] = {}
    for word, positions in value.items():
        if not isinstance(positions, list):
            continue
        for position in positions:
            try:
                words[int(position)] = str(word)
            except (TypeError, ValueError):
                continue
    if not words:
        return None
    return " ".join(words[index] for index in sorted(words))


def source_id(value: Any, *, prefix: str | None = None) -> str | None:
    candidate = text(value)
    if not candidate:
        return None
    if candidate.startswith("http"):
        candidate = candidate.rstrip("/").rsplit("/", 1)[-1]
    if prefix and candidate.lower().startswith(prefix.lower()):
        candidate = candidate[len(prefix):]
    return candidate or None
