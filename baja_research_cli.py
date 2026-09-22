"""Standalone local entry point using the same engine as the Hermes plugin."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from config import config_from_env
from schemas import validate_search_args
from storage import ResearchStorage
from tools import ResearchService


def _default_db() -> Path:
    root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return root.expanduser() / "baja-research" / "baja_research.sqlite3"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BAJA Research local academic search")
    parser.add_argument("--db", type=Path, default=_default_db(), help="SQLite cache path")
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search", help="Search from one natural-language request")
    search.add_argument("request", help="E.g. '3 artigos sobre LoRa'")
    search.add_argument("--limit", type=int)
    search.add_argument("--year-from", type=int)
    search.add_argument("--year-to", type=int)
    search.add_argument("--refresh-cache", action="store_true")
    search.add_argument("--json", action="store_true", help="Print normalized JSON")
    commands.add_parser("stats", help="Show local cache and source configuration")
    args = parser.parse_args(argv)
    config = config_from_env()
    service = ResearchService(
        config=config,
        storage=ResearchStorage(args.db, ttl_hours=config.cache_ttl_hours),
    )
    try:
        if args.command == "stats":
            print(json.dumps(service.cache_stats(), ensure_ascii=False, indent=2))
            return 0
        supplied = {
            "request": args.request,
            "year_from": args.year_from,
            "year_to": args.year_to,
            "refresh_cache": args.refresh_cache,
        }
        if args.limit is not None:
            supplied["limit"] = args.limit
        try:
            result = service.search(**validate_search_args(supplied))
        except ValueError as exc:
            parser.error(str(exc))
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"{result['returned']} trabalho(s) verificado(s) para: {args.request}")
            for number, paper in enumerate(result["results"], 1):
                evidence = paper.get("access_verification") or {}
                print(f"\n{number}. {paper['title']}")
                print(f"   {paper.get('document_type') or 'tipo não informado'} | "
                      f"{paper.get('year') or 'ano não informado'} | "
                      f"{evidence.get('page_count', '?')} páginas")
                print(f"   PDF: {paper['full_text_url']}")
                if paper.get("doi"):
                    print(f"   DOI: {paper['doi']}")
            degraded = [source for source, status in result["sources"].items()
                        if status.get("status") in {"error", "partial"}]
            if degraded:
                print("\nFontes degradadas: " + ", ".join(degraded))
            if not result["results"]:
                print("Nenhum trabalho satisfez tema Baja/off-road e PDF gratuito completo verificado.")
        return 0 if result["results"] else 1
    finally:
        service.close()


if __name__ == "__main__":
    sys.exit(main())
