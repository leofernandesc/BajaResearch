"""arXiv supplement uses its Atom API and never promotes an unverified URL."""

import httpx
import pytest

from clients.arxiv import ArxivClient
from clients.base import SourceError


ATOM = b'''<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
 <entry><id>https://arxiv.org/abs/2501.01234</id>
  <title>Telemetry methods for Formula SAE racing vehicles</title>
  <summary>A telemetry preprint for racing.</summary>
  <published>2025-01-01T00:00:00Z</published>
  <author><name>A. Researcher</name></author>
  <link title="pdf" href="https://arxiv.org/pdf/2501.01234" type="application/pdf" />
  <arxiv:doi>10.1234/example</arxiv:doi>
 </entry>
</feed>'''


def test_arxiv_atom_normalization_and_query(monkeypatch):
    monkeypatch.setattr("clients.arxiv._LAST_REQUEST", 0.0)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=ATOM)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = ArxivClient(http_client=http)
    papers = client.search("Formula SAE telemetry", limit=3)
    assert len(papers) == 1
    assert papers[0].title == "Telemetry methods for Formula SAE racing vehicles"
    assert papers[0].year == 2025
    assert papers[0].doi == "10.1234/example"
    assert papers[0].open_access_url == "https://arxiv.org/pdf/2501.01234"
    assert papers[0].access_status != "verified_pdf"
    assert "Formula" in seen[0].url.params["search_query"]
    http.close()


def test_arxiv_429_is_structured(monkeypatch):
    monkeypatch.setattr("clients.arxiv._LAST_REQUEST", 0.0)
    http = httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(429)))
    client = ArxivClient(http_client=http)
    with pytest.raises(SourceError) as error:
        client.search("Formula SAE telemetry")
    assert error.value.code == "rate_limited"
    assert error.value.status == 429
    http.close()
