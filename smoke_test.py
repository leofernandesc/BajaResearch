#!/usr/bin/env python3
"""Run a real, human-readable BAJA Research query against the public APIs."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from storage import ResearchStorage
from tools import ResearchConfig, ResearchService


DEFAULT_QUERY = "telemetry data acquisition Formula SAE"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--query",
        action="append",
        help="Technical query; repeat to test complementary queries.",
    )
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--year-from", type=int)
    parser.add_argument("--year-to", type=int)
    access_group = parser.add_mutually_exclusive_group()
    access_group.add_argument(
        "--open-access-only",
        action="store_true",
        help="Explicitly keep the default: only verified open-access papers.",
    )
    access_group.add_argument(
        "--include-paywalled",
        action="store_true",
        help="Include non-open-access records for diagnostic comparison.",
    )
    parser.add_argument(
        "--db",
        default=str(Path(".baja-research-smoke.sqlite3").resolve()),
        help="SQLite path used by the smoke test.",
    )
    args = parser.parse_args()
    queries = args.query or [DEFAULT_QUERY]
    service = ResearchService(
        config=ResearchConfig(),
        storage=ResearchStorage(args.db, ttl_hours=0),
    )
    try:
        response = service.search(
            queries=queries,
            limit=args.limit,
            year_from=args.year_from,
            year_to=args.year_to,
            open_access_only=not args.include_paywalled,
            refresh_cache=True,
        )
    finally:
        service.close()

    print(f"ok={response.get('ok')} cache_hit={response['cache']['hit']} total_found={response['total_found']}")
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
        print(f"   doi: {paper.get('doi') or 'não informado'}")
        print(f"   url: {paper.get('open_access_url') or paper.get('url') or 'não informado'}")
        print(f"   score: {paper.get('ranking_score')}")
    return 0 if response.get("ok") or response.get("results") else 1


if __name__ == "__main__":
    sys.exit(main())
