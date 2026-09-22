#!/usr/bin/env python3
"""Run a real, human-readable BAJA Research query against the public APIs."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from storage import ResearchStorage
from config import config_from_env
from tools import ResearchService


DEFAULT_QUERY = "telemetry data acquisition Formula SAE"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--query",
        action="append",
        help="Technical query; repeat to test complementary queries.",
    )
    parser.add_argument(
        "--technical-focus",
        help="Topic used by the hard relevance gate; defaults to the first query.",
    )
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--year-from", type=int)
    parser.add_argument("--year-to", type=int)
    parser.add_argument(
        "--refresh-cache", action="store_true",
        help="Force a live search rather than reuse a fresh identical search.",
    )
    parser.add_argument(
        "--document-type",
        choices=("any", "bachelor_thesis", "long_form", "articles"),
        help="Strict work type; inferred from TCC/artigo query words when omitted.",
    )
    parser.add_argument(
        "--document-preference",
        choices=("long_form_first", "articles_first"),
        default="long_form_first",
        help="Order document classes after strict relevance and PDF checks.",
    )
    parser.add_argument(
        "--include-electric-vehicles",
        action="store_true",
        help="Allow EV/hybrid/fuel-cell work only for an explicit EV smoke test.",
    )
    parser.add_argument(
        "--db",
        default=str(Path(".baja-research-smoke.sqlite3").resolve()),
        help="SQLite path used by the smoke test.",
    )
    args = parser.parse_args()
    queries = args.query or [DEFAULT_QUERY]
    service = ResearchService(
        config=config_from_env(),
        storage=ResearchStorage(args.db),
    )
    try:
        response = service.search(
            queries=queries,
            original_query=queries[0],
            technical_focus=args.technical_focus,
            document_type=args.document_type,
            limit=args.limit,
            year_from=args.year_from,
            year_to=args.year_to,
            document_preference=args.document_preference,
            exclude_electric_vehicles=not args.include_electric_vehicles,
            refresh_cache=args.refresh_cache,
        )
    finally:
        service.close()

    print(
        f"ok={response.get('ok')} cache_hit={response['cache']['hit']} "
        f"total_found={response['total_found']} returned={response['returned']}"
    )
    print(
        "policy="
        f"{response.get('policy', {})} more_available={response.get('more_available', 0)}"
    )
    print("\nFontes:")
    for source, status in response.get("sources", {}).items():
        print(
            f"- {source}: {status['status']} | results={status['result_count']} "
            f"| latency_ms={status['latency_ms']} | errors={len(status['errors'])}"
        )
    if response.get("warnings"):
        print("\nAvisos:")
        for warning in response["warnings"]:
            print(f"- {warning}")
    print("\nResultados normalizados:")
    for index, paper in enumerate(response.get("results", []), start=1):
        authors = ", ".join(paper.get("authors", [])[:3])
        if len(paper.get("authors", [])) > 3:
            authors += ", et al."
        print(f"\n{index}. {paper.get('title')}")
        print(f"   autores: {authors or 'não informado'}")
        print(f"   ano/venue: {paper.get('year') or 'n/i'} / {paper.get('venue') or 'n/i'}")
        print(
            f"   tipo/instituição: {paper.get('document_type') or 'n/i'} / "
            f"{paper.get('institution') or 'n/i'}"
        )
        print(f"   doi: {paper.get('doi') or 'não informado'}")
        print(f"   PDF gratuito verificado: {paper.get('full_text_url') or 'não informado'}")
        print(f"   acesso: {paper.get('access_status') or 'não informado'}")
        print(f"   score: {paper.get('ranking_score')}")
    return 0 if response.get("results") else 1


if __name__ == "__main__":
    sys.exit(main())
