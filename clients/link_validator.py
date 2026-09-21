"""Strict anonymous full-text PDF verification with SSRF safeguards."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
import ipaddress
import logging
import socket
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlparse

import httpx

from .http import USER_AGENT


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccessCheck:
    """Evidence that a URL is or is not an anonymously downloadable PDF."""

    status: str
    url: str
    final_url: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    reason: str | None = None
    checked_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": self.status,
            "url": self.url,
            "checked_at": self.checked_at,
            "evidence": dict(self.evidence),
        }
        if self.final_url:
            result["final_url"] = self.final_url
        if self.http_status is not None:
            result["http_status"] = self.http_status
        if self.content_type:
            result["content_type"] = self.content_type
        if self.reason:
            result["reason"] = self.reason
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AccessCheck":
        return cls(
            status=str(value.get("status") or "temporary_error"),
            url=str(value.get("url") or ""),
            final_url=value.get("final_url"),
            http_status=value.get("http_status"),
            content_type=value.get("content_type"),
            reason=value.get("reason"),
            checked_at=str(value.get("checked_at") or datetime.now(timezone.utc).isoformat()),
            evidence=dict(value.get("evidence") or {}),
        )


def _default_resolver(host: str, port: int) -> Iterable[str]:
    return {
        item[4][0]
        for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if item and item[4]
    }


def _unsafe_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return True
    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


class PdfAccessVerifier:
    """Confirm PDF bytes without downloading the complete document.

    Every initial target and redirect is checked before requesting it. A
    bibliographic landing page, DOI redirect, OA flag or HTTP 200 alone is not
    sufficient evidence of free full text.
    """

    def __init__(
        self,
        *,
        timeout: float = 8.0,
        max_redirects: int = 5,
        sample_bytes: int = 8192,
        http_client: httpx.Client | None = None,
        resolver: Callable[[str, int], Iterable[str]] | None = None,
        storage: Any | None = None,
        valid_ttl_hours: float = 168.0,
        invalid_ttl_hours: float = 24.0,
        temporary_ttl_hours: float = 1.0,
    ) -> None:
        self.timeout = max(1.0, min(float(timeout), 30.0))
        self.max_redirects = max(0, min(int(max_redirects), 10))
        self.sample_bytes = max(512, min(int(sample_bytes), 65536))
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            timeout=self.timeout,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept": "application/pdf,*/*;q=0.2"},
        )
        self._resolver = resolver or _default_resolver
        self.storage = storage
        self.valid_ttl_hours = max(0.0, float(valid_ttl_hours))
        self.invalid_ttl_hours = max(0.0, float(invalid_ttl_hours))
        self.temporary_ttl_hours = max(0.0, float(temporary_ttl_hours))
        self._cache: dict[str, AccessCheck] = {}

    def _target_error(self, url: str) -> tuple[str, str] | None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return "invalid", "unsupported_or_malformed_url"
        if parsed.username or parsed.password:
            return "blocked", "credentials_in_url"
        host = parsed.hostname.rstrip(".").casefold()
        if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
            return "blocked", "private_network_target"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            addresses = list(self._resolver(host, port))
        except (OSError, socket.gaierror):
            return "temporary_error", "dns_resolution_failed"
        if not addresses:
            return "temporary_error", "dns_resolution_failed"
        if any(_unsafe_ip(address) for address in addresses):
            return "blocked", "private_network_target"
        return None

    def _cached(self, url: str) -> AccessCheck | None:
        if url in self._cache:
            return self._cache[url]
        if self.storage is None:
            return None
        value = self.storage.get_access_check(
            url,
            valid_ttl_hours=self.valid_ttl_hours,
            invalid_ttl_hours=self.invalid_ttl_hours,
            temporary_ttl_hours=self.temporary_ttl_hours,
        )
        if value is None:
            return None
        result = AccessCheck.from_dict(value)
        self._cache[url] = result
        return result

    def _store(self, result: AccessCheck) -> AccessCheck:
        self._cache[result.url] = result
        if self.storage is not None:
            self.storage.save_access_check(result.to_dict())
        return result

    def check(self, url: str | None) -> AccessCheck:
        raw = str(url or "").strip()
        if not raw:
            return AccessCheck("not_provided", "", reason="not_provided")
        cached = self._cached(raw)
        if cached is not None:
            return cached

        current = raw
        redirects: list[str] = []
        for redirect_count in range(self.max_redirects + 1):
            target_error = self._target_error(current)
            if target_error is not None:
                status, reason = target_error
                return self._store(
                    AccessCheck(
                        status,
                        raw,
                        final_url=current,
                        reason=reason,
                        evidence={"redirects": redirects},
                    )
                )
            try:
                with self._client.stream(
                    "GET",
                    current,
                    headers={
                        "Range": f"bytes=0-{self.sample_bytes - 1}",
                        "Accept": "application/pdf,*/*;q=0.2",
                    },
                    follow_redirects=False,
                    timeout=self.timeout,
                ) as response:
                    status_code = response.status_code
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            return self._store(
                                AccessCheck(
                                    "invalid",
                                    raw,
                                    final_url=current,
                                    http_status=status_code,
                                    content_type=content_type or None,
                                    reason="redirect_without_location",
                                    evidence={"redirects": redirects},
                                )
                            )
                        if redirect_count >= self.max_redirects:
                            return self._store(
                                AccessCheck(
                                    "invalid",
                                    raw,
                                    final_url=current,
                                    http_status=status_code,
                                    reason="too_many_redirects",
                                    evidence={"redirects": redirects},
                                )
                            )
                        current = urljoin(current, location)
                        redirects.append(current)
                        continue
                    if status_code in {403, 408, 425, 429} or status_code >= 500:
                        return self._store(
                            AccessCheck(
                                "temporary_error",
                                raw,
                                final_url=current,
                                http_status=status_code,
                                content_type=content_type or None,
                                reason="temporarily_unavailable",
                                evidence={"redirects": redirects},
                            )
                        )
                    if status_code not in {200, 206}:
                        return self._store(
                            AccessCheck(
                                "invalid",
                                raw,
                                final_url=current,
                                http_status=status_code,
                                content_type=content_type or None,
                                reason="http_error",
                                evidence={"redirects": redirects},
                            )
                        )
                    sample = bytearray()
                    for chunk in response.iter_bytes():
                        remaining = self.sample_bytes - len(sample)
                        if remaining <= 0:
                            break
                        sample.extend(chunk[:remaining])
                        if len(sample) >= self.sample_bytes:
                            break
                    pdf_magic = bytes(sample).lstrip().startswith(b"%PDF-")
                    pdf_content_type = content_type == "application/pdf" or content_type.endswith("+pdf")
                    if not (pdf_magic or pdf_content_type):
                        return self._store(
                            AccessCheck(
                                "invalid",
                                raw,
                                final_url=current,
                                http_status=status_code,
                                content_type=content_type or None,
                                reason="response_is_not_pdf",
                                evidence={
                                    "redirects": redirects,
                                    "bytes_sampled": len(sample),
                                    "pdf_magic": pdf_magic,
                                },
                            )
                        )
                    method = (
                        "streamed_get_pdf_magic"
                        if pdf_magic
                        else "streamed_get_pdf_content_type"
                    )
                    return self._store(
                        AccessCheck(
                            "verified_pdf",
                            raw,
                            final_url=current,
                            http_status=status_code,
                            content_type=content_type or None,
                            evidence={
                                "method": method,
                                "redirects": redirects,
                                "bytes_sampled": len(sample),
                                "pdf_magic": pdf_magic,
                                "anonymous": True,
                            },
                        )
                    )
            except httpx.TimeoutException:
                return self._store(
                    AccessCheck(
                        "temporary_error",
                        raw,
                        final_url=current,
                        reason="timeout",
                        evidence={"redirects": redirects},
                    )
                )
            except httpx.RequestError:
                return self._store(
                    AccessCheck(
                        "temporary_error",
                        raw,
                        final_url=current,
                        reason="network_error",
                        evidence={"redirects": redirects},
                    )
                )

        return self._store(
            AccessCheck("invalid", raw, final_url=current, reason="too_many_redirects")
        )

    def check_many(self, urls: list[str]) -> dict[str, AccessCheck]:
        unique = list(dict.fromkeys(url for url in urls if url))
        if not unique:
            return {}
        results: dict[str, AccessCheck] = {}
        with ThreadPoolExecutor(
            max_workers=min(4, len(unique)), thread_name_prefix="baja-pdf"
        ) as executor:
            futures = {executor.submit(self.check, url): url for url in unique}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    results[url] = future.result()
                except Exception:
                    logger.debug("PDF verification failed", exc_info=True)
                    results[url] = AccessCheck(
                        "temporary_error", url, reason="verifier_error"
                    )
        return results

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


# Compatibility aliases for the v0.1 public module path.
LinkCheck = AccessCheck
LinkValidator = PdfAccessVerifier


__all__ = ["AccessCheck", "PdfAccessVerifier", "LinkCheck", "LinkValidator"]
