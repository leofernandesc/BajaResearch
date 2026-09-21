"""Brazilian Digital Library of Theses and Dissertations client."""

from __future__ import annotations

from .http import JsonHttpClient
from .vufind import VuFindClient


BASE_URL = "https://bdtd.ibict.br/vufind/api/v1"


class BdtdClient(VuFindClient):
    source = "bdtd"

    def __init__(
        self,
        *,
        timeout: float = 8.0,
        max_retries: int = 1,
        http: JsonHttpClient | None = None,
    ) -> None:
        super().__init__(
            BASE_URL,
            self.source,
            timeout=timeout,
            max_retries=max_retries,
            http=http,
        )


__all__ = ["BdtdClient"]
