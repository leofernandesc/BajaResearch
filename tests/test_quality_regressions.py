"""Versioned acceptance fixtures for failures observed in real WhatsApp use."""

from __future__ import annotations

import json
from pathlib import Path


FIXTURE = Path(__file__).parent / "fixtures" / "quality_regressions.json"


def test_quality_regression_fixture_is_complete_and_unique() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert payload["version"] == 1
    cases = payload["cases"]
    assert len(cases) >= 6
    assert len({case["id"] for case in cases}) == len(cases)
    assert {case["expected"] for case in cases} >= {
        "accept_long_form",
        "accept_free_pdf",
        "reject_electric_vehicle",
        "reject_not_pdf",
        "reject_off_topic",
        "reject_wrong_technical_focus",
    }
    for case in cases:
        assert case["title"].strip()
        assert case["technical_focus"].strip()
        assert case["application_context"].strip()
