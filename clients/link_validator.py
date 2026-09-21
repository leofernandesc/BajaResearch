"""Conservative public-link validation for URLs exposed to Hermes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from .http import USER_AGENT


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LinkCheck:
    """Result of checking one public HTTP(S) URL."""

    status: str
    url: str
    final_url: str | None = None
    http_status: int | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"status": self.status}
        if self.final_url:
            result["final_url"] = self.final_url
        if self.http_status is not None:
            result["http_status"] = self.http_status
        if self.reason:
            result["reason"] = self.reason
        return result


def _is_success(status_code: int) -> bool:
    return 200 <= status_code < 400


class LinkValidator:
    """Check only the small set of links that will be returned to the user.

    A link is exposed only after a successful HEAD or lightweight GET. Network
    failures and rate limits are deliberately treated as ``unknown`` and are
    not exposed as usable links.
    """

    def __init__(
        self,
        *,
        timeout: float = 6.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            timeout=max(1.0, min(float(timeout), 30.0)),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        )
        self._cache: dict[str, LinkCheck] = {}

    @staticmethod
    def _invalid_url(url: str) -> LinkCheck | None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return LinkCheck("invalid", url, reason="unsupported_or_malformed_url")
        return None

    def _request(self, method: str, url: str) -> httpx.Response | None:
        try:
            return self._client.request(
                method,
                url,
                headers={"Range": "bytes=0-0"} if method == "GET" else None,
            )
        except httpx.RequestError:
            return None

    def check(self, url: str | None) -> LinkCheck:
        raw = str(url or "").strip()
        if not raw:
            return LinkCheck("not_provided", "", reason="not_provided")
        if raw in self._cache:
            return self._cache[raw]

        malformed = self._invalid_url(raw)
        if malformed is not None:
            self._cache[raw] = malformed
            return malformed

        response = self._request("HEAD", raw)
        # Many repositories reject HEAD even though their GET endpoint works.
        if response is None:
            result = LinkCheck("unknown", raw, reason="network_error")
        elif response.status_code in {403, 405, 501}:
            fallback = self._request("GET", raw)
            if fallback is not None:
                response = fallback
            result = self._result_from_response(raw, response)
        else:
            result = self._result_from_response(raw, response)

        self._cache[raw] = result
        return result

    @staticmethod
    def _result_from_response(url: str, response: httpx.Response) -> LinkCheck:
        final_url = str(response.url)
        if _is_success(response.status_code):
            return LinkCheck(
                "valid",
                url,
                final_url=final_url,
                http_status=response.status_code,
            )
        if response.status_code == 429 or response.status_code >= 500:
            return LinkCheck(
                "unknown",
                url,
                final_url=final_url,
                http_status=response.status_code,
                reason="temporarily_unavailable",
            )
        return LinkCheck(
            "invalid",
            url,
            final_url=final_url,
            http_status=response.status_code,
            reason="http_error",
        )

    def check_many(self, urls: list[str]) -> dict[str, LinkCheck]:
        unique = list(dict.fromkeys(url for url in urls if url))
        if not unique:
            return {}
        results: dict[str, LinkCheck] = {}
        with ThreadPoolExecutor(max_workers=min(8, len(unique)), thread_name_prefix="baja-links") as executor:
            futures = {executor.submit(self.check, url): url for url in unique}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    results[url] = future.result()
                except Exception:
                    logger.debug("link validation failed", exc_info=True)
                    results[url] = LinkCheck("unknown", url, reason="validator_error")
        return results

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


__all__ = ["LinkCheck", "LinkValidator"]
