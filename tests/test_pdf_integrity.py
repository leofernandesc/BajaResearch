"""Full-download access verification must never accept a PDF header alone."""

import httpx
import pytest

from clients.link_validator import PdfAccessVerifier
from storage import ResearchStorage
from tests.pdf_bytes import PDF_BYTES, make_pdf_bytes


def _verifier(content: bytes, *, status=200, headers=None, max_bytes=1_000_000,
              max_total_bytes=1_000_000, storage=None):
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(
            status, headers={"Content-Type": "application/pdf", **(headers or {})},
            content=content, request=request,
        )
    ))
    verifier = PdfAccessVerifier(
        http_client=client, resolver=lambda _host, _port: ["8.8.8.8"],
        max_bytes=max_bytes, max_total_bytes=max_total_bytes, storage=storage,
    )
    return verifier, client


@pytest.mark.parametrize("content,reason", [
    (b"%PDF-1.7 header only", "response_is_not_complete_pdf"),
    (PDF_BYTES[:-10], "response_is_not_complete_pdf"),
    (b"<html>Subscribe to read</html>", "response_is_not_pdf"),
])
def test_incomplete_or_paywalled_payload_is_never_verified(content, reason):
    verifier, client = _verifier(content)
    try:
        result = verifier.check("https://repository.example/work.pdf")
    finally:
        verifier.close()
        client.close()
    assert result.status == "invalid"
    assert result.reason == reason


def test_integral_pdf_records_reproducible_evidence():
    verifier, client = _verifier(PDF_BYTES)
    try:
        result = verifier.check("https://repository.example/work.pdf")
    finally:
        verifier.close()
        client.close()
    assert result.status == "verified_pdf"
    assert result.evidence["full_download"] is True
    assert result.evidence["downloaded_bytes"] == len(PDF_BYTES)
    assert result.evidence["page_count"] == 2
    assert len(result.evidence["sha256"]) == 64


def test_partial_content_cannot_claim_a_complete_pdf():
    verifier, client = _verifier(
        PDF_BYTES[:50], status=206,
        headers={"Content-Range": f"bytes 0-49/{len(PDF_BYTES)}"},
    )
    try:
        result = verifier.check("https://repository.example/work.pdf")
    finally:
        verifier.close()
        client.close()
    assert result.reason == "partial_pdf_response"


def test_file_and_search_budgets_are_enforced():
    verifier, client = _verifier(make_pdf_bytes(10), max_bytes=1024, max_total_bytes=1024)
    try:
        result = verifier.check("https://repository.example/work.pdf")
    finally:
        verifier.close()
        client.close()
    assert result.status == "invalid"
    assert result.reason == "pdf_size_limit_exceeded"


def test_old_header_only_access_cache_is_rechecked(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite3")
    storage.save_access_check({
        "url": "https://repository.example/work.pdf", "status": "verified_pdf",
        "evidence": {"pdf_magic": True},
    })
    verifier, client = _verifier(PDF_BYTES, storage=storage)
    try:
        result = verifier.check("https://repository.example/work.pdf")
    finally:
        verifier.close()
        client.close()
    assert result.evidence["full_download"] is True
