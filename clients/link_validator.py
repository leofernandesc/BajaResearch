"""Strict anonymous full-text PDF verification with SSRF safeguards."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import logging
import re
import socket
import tempfile
import threading
import time
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlparse

import httpx
from pypdf import PdfReader
from pypdf.errors import PdfReadError

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
    """Verify an anonymously downloaded PDF through its final byte and pages.

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
        max_bytes: int = 40 * 1024 * 1024,
        max_total_bytes: int = 160 * 1024 * 1024,
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
        self.max_bytes = max(1024, int(max_bytes))
        self.max_total_bytes = max(self.max_bytes, int(max_total_bytes))
        self._budget_lock = threading.Lock()
        self._budget_remaining = self.max_total_bytes
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

    def reset_download_budget(self) -> None:
        """Start one bounded search; the plugin has one local user in this MVP."""
        with self._budget_lock:
            self._budget_remaining = self.max_total_bytes

    def _charge(self, amount: int) -> bool:
        with self._budget_lock:
            if amount > self._budget_remaining:
                return False
            self._budget_remaining -= amount
            return True

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
        local = self._cache.get(url)
        if local is not None:
            try:
                age = datetime.now(timezone.utc) - datetime.fromisoformat(local.checked_at)
            except ValueError:
                age = timedelta.max
            ttl = (
                self.valid_ttl_hours if local.status == "verified_pdf"
                else self.temporary_ttl_hours if local.status == "temporary_error"
                else self.invalid_ttl_hours
            )
            if age < timedelta(hours=ttl) and (
                local.status != "verified_pdf" or local.evidence.get("full_download")
            ):
                return local
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
        # Older versions accepted a PDF MIME header without PDF bytes. Such
        # entries must be rechecked under the stricter policy.
        if result.status == "verified_pdf" and not result.evidence.get("full_download"):
            return None
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
        started = time.monotonic()
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
                        "Accept": "application/pdf,*/*;q=0.2",
                        "Accept-Encoding": "identity",
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
                    declared = response.headers.get("content-length")
                    try:
                        declared_size = int(declared) if declared is not None else None
                    except ValueError:
                        declared_size = None
                    if declared_size is not None and declared_size > self.max_bytes:
                        return self._store(AccessCheck(
                            "invalid", raw, final_url=current, http_status=status_code,
                            reason="pdf_size_limit_exceeded", evidence={"redirects": redirects},
                        ))
                    complete_range = None
                    if status_code == 206:
                        match = re.fullmatch(
                            r"bytes\s+0-(\d+)/(\d+)",
                            response.headers.get("content-range", "").strip(),
                            re.IGNORECASE,
                        )
                        if match is None or int(match.group(1)) + 1 != int(match.group(2)):
                            return self._store(AccessCheck(
                                "invalid", raw, final_url=current, http_status=status_code,
                                reason="partial_pdf_response", evidence={"redirects": redirects},
                            ))
                        complete_range = int(match.group(2))
                    digest = hashlib.sha256()
                    downloaded = 0
                    with tempfile.SpooledTemporaryFile(max_size=4 * 1024 * 1024, mode="w+b") as pdf_file:
                        for chunk in response.iter_bytes():
                            if not chunk:
                                continue
                            downloaded += len(chunk)
                            if downloaded > self.max_bytes:
                                return self._store(AccessCheck(
                                    "invalid", raw, final_url=current, http_status=status_code,
                                    reason="pdf_size_limit_exceeded", evidence={"redirects": redirects},
                                ))
                            if time.monotonic() - started > self.timeout:
                                return self._store(AccessCheck(
                                    "temporary_error", raw, final_url=current,
                                    reason="download_timeout", evidence={"redirects": redirects},
                                ))
                            if not self._charge(len(chunk)):
                                return self._store(AccessCheck(
                                    "temporary_error", raw, final_url=current,
                                    reason="search_download_budget_exhausted",
                                    evidence={"redirects": redirects},
                                ))
                            digest.update(chunk)
                            pdf_file.write(chunk)
                        if (declared_size is not None and downloaded != declared_size) or (
                            complete_range is not None and downloaded != complete_range
                        ):
                            return self._store(AccessCheck(
                                "invalid", raw, final_url=current, http_status=status_code,
                                reason="truncated_pdf_response",
                                evidence={"redirects": redirects, "downloaded_bytes": downloaded},
                            ))
                        pdf_file.seek(0)
                        pdf_magic = pdf_file.read(5) == b"%PDF-"
                        pdf_file.seek(max(0, downloaded - 4096))
                        pdf_eof = b"%%EOF" in pdf_file.read()
                        if not pdf_magic or not pdf_eof:
                            return self._store(AccessCheck(
                                "invalid", raw, final_url=current, http_status=status_code,
                                content_type=content_type or None,
                                reason=("response_is_not_pdf" if not pdf_magic
                                        else "response_is_not_complete_pdf"),
                                evidence={"redirects": redirects, "pdf_magic": pdf_magic,
                                          "pdf_eof": pdf_eof, "downloaded_bytes": downloaded},
                            ))
                        pdf_file.seek(0)
                        try:
                            reader = PdfReader(pdf_file, strict=True)
                            if reader.is_encrypted:
                                raise PdfReadError("encrypted_pdf")
                            page_count = len(reader.pages)
                            if page_count < 1:
                                raise PdfReadError("no_pages")
                            reader.pages[-1]  # ensure the page tree resolves to the end
                        except (PdfReadError, ValueError, KeyError, TypeError, OSError):
                            return self._store(AccessCheck(
                                "invalid", raw, final_url=current, http_status=status_code,
                                reason="pdf_structure_invalid_or_encrypted",
                                evidence={"redirects": redirects, "downloaded_bytes": downloaded},
                            ))
                    return self._store(AccessCheck(
                        "verified_pdf", raw, final_url=current, http_status=status_code,
                        content_type=content_type or None,
                        evidence={
                            "method": "anonymous_full_get_and_pdf_parse",
                            "redirects": redirects,
                            "downloaded_bytes": downloaded,
                            "sha256": digest.hexdigest(),
                            "page_count": page_count,
                            "pdf_magic": True,
                            "pdf_eof": True,
                            "full_download": True,
                            "anonymous": True,
                        },
                    ))
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
            max_workers=min(3, len(unique)), thread_name_prefix="baja-pdf"
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
