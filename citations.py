"""Conservative citation formatting from normalized source metadata."""

from __future__ import annotations

import re

try:
    from .models import Paper
except ImportError:  # pragma: no cover - direct module imports
    from models import Paper


def _author_for_bibtex(name: str) -> str:
    return name.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _bibtex_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def format_abnt(paper: Paper) -> str:
    parts: list[str] = []
    if paper.authors:
        parts.append("; ".join(paper.authors))
    if paper.title:
        parts.append(paper.title)
    if paper.venue:
        parts.append(paper.venue)
    if paper.year is not None:
        parts.append(str(paper.year))
    if paper.doi:
        parts.append(f"DOI: {paper.doi}")
    else:
        public_url = paper.to_dict(compact=True).get("url")
        if public_url:
            parts.append(f"Disponível em: {public_url}")
    return ". ".join(parts) + ("." if parts else "")


def format_bibtex(paper: Paper) -> str:
    first_author = paper.authors[0].split()[-1].lower() if paper.authors else "paper"
    slug = re.sub(r"[^a-z0-9]+", "", first_author) or "paper"
    key = f"{slug}{paper.year or ''}"
    fields: list[tuple[str, str]] = [("title", paper.title)]
    if paper.authors:
        fields.append(
            ("author", " and ".join(_author_for_bibtex(author) for author in paper.authors))
        )
    if paper.year is not None:
        fields.append(("year", str(paper.year)))
    if paper.venue:
        fields.append(("journal", paper.venue))
    if paper.doi:
        fields.append(("doi", paper.doi))
    else:
        public_url = paper.to_dict(compact=True).get("url")
        if public_url:
            fields.append(("url", public_url))
    lines = [f"@article{{{key},"]
    lines.extend(f"  {name} = {{{_bibtex_value(value)}}}," for name, value in fields)
    lines.append("}")
    return "\n".join(lines)


__all__ = ["format_abnt", "format_bibtex"]
