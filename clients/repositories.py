"""Resolve DSpace repository landing pages to original PDF bitstreams."""

from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import quote, urlparse

import httpx

from .http import USER_AGENT
try:
    from ..models import Paper, normalize_document_type
except ImportError:  # pragma: no cover
    from models import Paper, normalize_document_type


_ENTITY_RE = re.compile(r"/(?:entities/[^/]+|items)/([0-9a-f-]{32,36})(?:/|$)", re.I)
_HANDLE_RE = re.compile(r"/(?:xmlui/|jspui/)?handle/(.+)$", re.I)


def _first(values: Any) -> str | None:
    if not isinstance(values, list):
        return None
    for value in values:
        if isinstance(value, Mapping):
            value = value.get("value")
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _all(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        if isinstance(value, Mapping):
            value = value.get("value")
        if value is not None and str(value).strip():
            result.append(str(value).strip())
    return result


class RepositoryResolver:
    """Use repository APIs only; no full PDF download and no Scholar scraping."""

    def __init__(
        self,
        verifier: Any,
        *,
        timeout: float = 8.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.verifier = verifier
        self.timeout = max(1.0, min(float(timeout), 30.0))
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            timeout=self.timeout,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )

    def _json(self, url: str) -> Any | None:
        try:
            response = self._client.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                follow_redirects=False,
                timeout=self.timeout,
            )
        except httpx.RequestError:
            return None
        if response.status_code != 200:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    @staticmethod
    def _origin(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _enrich_dspace7(self, paper: Paper, landing: str, item_id: str) -> list[str]:
        origin = self._origin(landing)
        item = self._json(f"{origin}/server/api/core/items/{item_id}")
        if not isinstance(item, Mapping):
            return []
        metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
        paper.abstract = paper.abstract or _first(metadata.get("dc.description.abstract"))
        paper.institution = paper.institution or _first(
            metadata.get("dc.contributor.institution")
        ) or _first(metadata.get("dc.publisher"))
        paper.language = paper.language or _first(metadata.get("dc.language.iso"))
        paper.document_type = normalize_document_type(
            _first(metadata.get("dc.type")) or paper.document_type
        )
        issued = _first(metadata.get("dc.date.issued"))
        if paper.year is None and issued:
            match = re.search(r"\b(19|20)\d{2}\b", issued)
            if match:
                paper.year = int(match.group(0))
        paper.topics = list(
            dict.fromkeys([*paper.topics, *_all(metadata.get("dc.subject"))])
        )
        if not paper.authors:
            paper.authors = _all(metadata.get("dc.contributor.author"))
        extent = _first(metadata.get("dc.format.extent"))
        if extent:
            paper.metadata["extent"] = extent
        paper.provenance.setdefault("repository", {})["dspace_version"] = 7

        bundles = self._json(
            f"{origin}/server/api/core/items/{item_id}/bundles?size=20"
        )
        embedded = bundles.get("_embedded") if isinstance(bundles, Mapping) else None
        values = embedded.get("bundles") if isinstance(embedded, Mapping) else None
        if not isinstance(values, list):
            return []
        candidates: list[str] = []
        for bundle in values:
            if not isinstance(bundle, Mapping) or str(bundle.get("name") or "").upper() != "ORIGINAL":
                continue
            links = bundle.get("_links") if isinstance(bundle.get("_links"), Mapping) else {}
            bitstreams_link = links.get("bitstreams") if isinstance(links.get("bitstreams"), Mapping) else {}
            href = bitstreams_link.get("href")
            if not href:
                continue
            bitstreams = self._json(str(href))
            bit_embedded = bitstreams.get("_embedded") if isinstance(bitstreams, Mapping) else None
            items = bit_embedded.get("bitstreams") if isinstance(bit_embedded, Mapping) else None
            for bitstream in items if isinstance(items, list) else []:
                if not isinstance(bitstream, Mapping):
                    continue
                name = str(bitstream.get("name") or "").casefold()
                content = bitstream.get("_links") if isinstance(bitstream.get("_links"), Mapping) else {}
                content = content.get("content") if isinstance(content.get("content"), Mapping) else {}
                href = content.get("href")
                if href and (name.endswith(".pdf") or not name):
                    candidates.append(str(href))
        return list(dict.fromkeys(candidates))

    def _enrich_dspace6(self, paper: Paper, landing: str, handle: str) -> list[str]:
        origin = self._origin(landing)
        item = self._json(f"{origin}/rest/handle/{quote(handle, safe='/')}")
        if not isinstance(item, Mapping) or not item.get("uuid"):
            return []
        item_id = str(item["uuid"])
        metadata = self._json(f"{origin}/rest/items/{item_id}/metadata")
        grouped: dict[str, list[str]] = {}
        if isinstance(metadata, list):
            for entry in metadata:
                if not isinstance(entry, Mapping):
                    continue
                key = str(entry.get("key") or "")
                value = entry.get("value")
                if key and value is not None:
                    grouped.setdefault(key, []).append(str(value))
        paper.abstract = paper.abstract or _first(grouped.get("dc.description.abstract"))
        paper.institution = paper.institution or _first(
            grouped.get("dc.contributor")
        ) or _first(grouped.get("dc.publisher"))
        paper.language = paper.language or _first(grouped.get("dc.language.iso"))
        paper.document_type = normalize_document_type(
            _first(grouped.get("dc.type")) or paper.document_type
        )
        issued = _first(grouped.get("dc.date.issued"))
        if paper.year is None and issued:
            match = re.search(r"\b(19|20)\d{2}\b", issued)
            if match:
                paper.year = int(match.group(0))
        paper.topics = list(
            dict.fromkeys([*paper.topics, *_all(grouped.get("dc.subject")), *_all(grouped.get("dc.subject.classification"))])
        )
        if not paper.authors:
            paper.authors = _all(grouped.get("dc.contributor.author"))
        extent = _first(grouped.get("dc.format.extent"))
        if extent:
            paper.metadata["extent"] = extent
        paper.provenance.setdefault("repository", {})["dspace_version"] = 6

        bitstreams = self._json(f"{origin}/rest/items/{item_id}/bitstreams")
        candidates: list[str] = []
        for bitstream in bitstreams if isinstance(bitstreams, list) else []:
            if not isinstance(bitstream, Mapping):
                continue
            if str(bitstream.get("bundleName") or "").upper() != "ORIGINAL":
                continue
            mime = str(bitstream.get("mimeType") or "").casefold()
            name = str(bitstream.get("name") or "").casefold()
            retrieve = bitstream.get("retrieveLink")
            if retrieve and (mime == "application/pdf" or name.endswith(".pdf")):
                candidates.append(f"{origin}{retrieve}")
        return list(dict.fromkeys(candidates))

    def resolve(self, paper: Paper) -> list[str]:
        landing = paper.landing_url or paper.url
        if not landing:
            return []
        landing_check = self.verifier.check(landing)
        if landing_check.status == "verified_pdf":
            return [landing_check.final_url or landing]
        final_landing = landing_check.final_url or landing
        parsed = urlparse(final_landing)
        entity = _ENTITY_RE.search(parsed.path)
        if entity:
            candidates = self._enrich_dspace7(paper, final_landing, entity.group(1))
        else:
            handle = _HANDLE_RE.search(parsed.path)
            candidates = (
                self._enrich_dspace6(paper, final_landing, handle.group(1))
                if handle
                else []
            )
        existing = list(paper.metadata.get("full_text_candidates") or [])
        paper.metadata["full_text_candidates"] = list(
            dict.fromkeys([*existing, *candidates])
        )
        return candidates

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


__all__ = ["RepositoryResolver"]
