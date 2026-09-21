"""Optional DOI-to-open-copy resolver; every returned URL is still verified."""

from __future__ import annotations

import os
from typing import Any, Mapping
from urllib.parse import quote

from .base import as_list, as_mapping, text
from .http import JsonHttpClient
try:
    from ..models import normalize_doi
except ImportError:  # pragma: no cover
    from models import normalize_doi


BASE_URL = "https://api.unpaywall.org/v2"


class UnpaywallClient:
    source = "unpaywall"

    def __init__(
        self,
        *,
        email: str | None = None,
        timeout: float = 15.0,
        max_retries: int = 1,
        http: JsonHttpClient | None = None,
    ) -> None:
        self.email = email or os.getenv("UNPAYWALL_EMAIL") or None
        self.http = http or JsonHttpClient(
            BASE_URL,
            self.source,
            timeout=timeout,
            max_retries=max_retries,
        )

    @property
    def configured(self) -> bool:
        return bool(self.email)

    def resolve(self, identifier: str) -> dict[str, Any]:
        doi = normalize_doi(identifier)
        if not doi or not self.email:
            return {"doi": doi, "candidates": [], "landing_urls": []}
        payload = self.http.get_json(
            f"/{quote(doi, safe='')}", params={"email": self.email}
        )
        root = as_mapping(payload)
        locations: list[Mapping[str, Any]] = []
        best = as_mapping(root.get("best_oa_location"))
        if best:
            locations.append(best)
        locations.extend(
            as_mapping(item) for item in as_list(root.get("oa_locations"))
        )
        candidates: list[str] = []
        landing_urls: list[str] = []
        for location in locations:
            pdf = text(location.get("url_for_pdf"))
            landing = text(location.get("url") or location.get("url_for_landing_page"))
            if pdf:
                candidates.append(pdf)
            if landing:
                landing_urls.append(landing)
        return {
            "doi": doi,
            "is_oa": bool(root.get("is_oa")),
            "candidates": list(dict.fromkeys(candidates)),
            "landing_urls": list(dict.fromkeys(landing_urls)),
            "oa_status": text(root.get("oa_status")),
        }

    def close(self) -> None:
        self.http.close()


__all__ = ["UnpaywallClient"]
