"""Acceptance tests for failures observed in real WhatsApp use."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from clients.link_validator import PdfAccessVerifier
from models import Paper, is_long_form_document
from ranking import filter_relevant_papers, is_electric_vehicle_paper
from tests.pdf_bytes import PDF_BYTES


FIXTURE = Path(__file__).parent / "fixtures" / "quality_regressions.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def test_quality_regression_fixture_is_complete_and_unique() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert payload["version"] == 2
    cases = payload["cases"]
    assert len(cases) >= 9
    assert len({case["id"] for case in cases}) == len(cases)
    assert {case["expected"] for case in cases} >= {
        "accept_long_form",
        "accept_free_pdf",
        "reject_electric_vehicle",
        "reject_not_pdf",
        "reject_off_topic",
        "reject_private_redirect",
        "reject_temporary_access",
        "reject_wrong_technical_focus",
    }
    for case in cases:
        assert case["title"].strip()
        assert case["technical_focus"].strip()
        assert case["application_context"].strip()


def _paper(case: dict) -> Paper:
    return Paper(
        "",
        case["title"],
        document_type=case.get("document_type"),
        topics=[case["application_context"]],
        open_access_url=case.get("full_text_url"),
        sources=["acceptance_fixture"],
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_quality_fixture_uses_production_relevance_rules(case: dict) -> None:
    paper = _paper(case)
    kept, rejected = filter_relevant_papers(
        [paper],
        case["technical_focus"],
        [f'{case["application_context"]} {case["technical_focus"]}'],
        require_context=True,
    )

    if case["expected"] == "reject_electric_vehicle":
        assert is_electric_vehicle_paper(paper) is True
        return
    assert is_electric_vehicle_paper(paper) is False
    if case["expected"] in {"reject_off_topic", "reject_wrong_technical_focus"}:
        assert kept == []
        assert rejected["wrong_technical_focus"] == 1
        return
    assert kept == [paper]
    if case["expected"] == "accept_long_form":
        assert is_long_form_document(paper.document_type) is True


ACCESS_CASES = [
    case
    for case in CASES
    if case["expected"]
    in {
        "accept_free_pdf",
        "reject_not_pdf",
        "reject_private_redirect",
        "reject_temporary_access",
    }
]


@pytest.mark.parametrize("case", ACCESS_CASES, ids=lambda case: case["id"])
def test_quality_fixture_uses_production_pdf_verifier(case: dict) -> None:
    initial_url = case["full_text_url"]
    redirect_url = case.get("redirect_url")

    def handler(request: httpx.Request) -> httpx.Response:
        if redirect_url and str(request.url) == initial_url:
            return httpx.Response(
                302, headers={"Location": redirect_url}, request=request
            )
        content_type = case.get("response_content_type", "text/html")
        prefix = case.get("response_prefix", "<html>login required</html>")
        content = PDF_BYTES if case["expected"] == "accept_free_pdf" else prefix.encode("utf-8")
        return httpx.Response(
            case.get("response_status", 200),
            headers={"Content-Type": content_type},
            content=content,
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    verifier = PdfAccessVerifier(
        http_client=client,
        resolver=lambda host, _port: [
            "127.0.0.1" if host == "127.0.0.1" else "8.8.8.8"
        ],
    )
    try:
        result = verifier.check(initial_url)
    finally:
        verifier.close()
        client.close()

    expected_status = {
        "accept_free_pdf": "verified_pdf",
        "reject_not_pdf": "invalid",
        "reject_private_redirect": "blocked",
        "reject_temporary_access": "temporary_error",
    }[case["expected"]]
    assert result.status == expected_status
