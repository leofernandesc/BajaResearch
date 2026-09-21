"""Small resilient JSON HTTP client shared by the academic APIs."""

from __future__ import annotations

import logging
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Any, Mapping

import httpx

from .base import SourceError

logger = logging.getLogger(__name__)

USER_AGENT = "BAJA-Research/0.2.0 (https://github.com/leofernandesc/BajaResearch)"


def _retry_after(headers: Mapping[str, Any]) -> float | None:
    value = headers.get("retry-after") or headers.get("Retry-After")
    if value is None:
        return None
    try:
        seconds = float(value)
        return max(0.0, min(seconds, 15.0))
    except (TypeError, ValueError):
        pass
    try:
        target = parsedate_to_datetime(str(value))
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        return max(0.0, min((target - datetime.now(timezone.utc)).total_seconds(), 15.0))
    except (TypeError, ValueError, OverflowError):
        return None


class JsonHttpClient:
    """GET JSON with bounded retries, rate-limit handling and safe errors."""

    def __init__(
        self,
        base_url: str,
        source: str,
        *,
        timeout: float = 8.0,
        max_retries: int = 1,
        headers: Mapping[str, str] | None = None,
        http_client: httpx.Client | None = None,
        sleep_fn=time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.source = source
        self.timeout = max(1.0, min(float(timeout), 120.0))
        self.max_retries = max(0, min(int(max_retries), 4))
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        self._headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if headers:
            self._headers.update({str(key): str(value) for key, value in headers.items()})
        self._sleep = sleep_fn

    def set_header(self, name: str, value: str) -> None:
        """Add a default header, including when a test/custom client is injected."""
        self._headers[str(name)] = str(value)

    def get_json(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        request_headers = dict(self._headers)
        if headers:
            request_headers.update({str(key): str(value) for key, value in headers.items()})

        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.get(url, params=dict(params or {}), headers=request_headers)
            except httpx.TimeoutException as exc:
                if attempt < self.max_retries:
                    self._backoff(attempt)
                    continue
                raise SourceError(
                    self.source,
                    "request timed out",
                    code="timeout",
                    retryable=True,
                ) from exc
            except httpx.RequestError as exc:
                if attempt < self.max_retries:
                    self._backoff(attempt)
                    continue
                raise SourceError(
                    self.source,
                    "request failed; check network connectivity",
                    code="network_error",
                    retryable=True,
                ) from exc

            retry_after = _retry_after(response.headers)
            if response.status_code == 429:
                if attempt < self.max_retries:
                    self._backoff(attempt, retry_after)
                    continue
                raise SourceError(
                    self.source,
                    "source rate limit reached (HTTP 429)",
                    status=429,
                    code="rate_limited",
                    retryable=True,
                    retry_after=retry_after,
                )
            if 500 <= response.status_code <= 599:
                if attempt < self.max_retries:
                    self._backoff(attempt, retry_after)
                    continue
                raise SourceError(
                    self.source,
                    f"source server error (HTTP {response.status_code})",
                    status=response.status_code,
                    code="upstream_5xx",
                    retryable=True,
                    retry_after=retry_after,
                )
            if response.status_code >= 400:
                raise SourceError(
                    self.source,
                    f"source rejected request (HTTP {response.status_code})",
                    status=response.status_code,
                    code="http_error",
                    retryable=False,
                )
            try:
                return response.json()
            except ValueError as exc:
                raise SourceError(
                    self.source,
                    "source returned invalid JSON",
                    status=response.status_code,
                    code="invalid_json",
                    retryable=False,
                ) from exc

        raise SourceError(self.source, "request failed", code="request_failed")

    def _backoff(self, attempt: int, retry_after: float | None = None) -> None:
        delay = retry_after if retry_after is not None else min(0.25 * (2**attempt), 2.0)
        if delay > 0:
            self._sleep(delay)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
