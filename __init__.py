"""BAJA Research Hermes Agent plugin.

The package is intentionally self-contained. Hermes loads this directory as a
namespaced plugin and calls :func:`register` through its public plugin API.
"""

from __future__ import annotations

from pathlib import Path

from .tools import ResearchService, build_tool_handlers


def register(ctx) -> None:
    """Register BAJA Research tools and its opt-in skill with Hermes."""
    service = ResearchService.from_hermes_context(ctx)

    for name, schema, handler, description in build_tool_handlers(service):
        ctx.register_tool(
            name=name,
            toolset="baja_research",
            schema=schema,
            handler=handler,
            description=description,
            emoji="📚",
        )

    skill_path = Path(__file__).parent / "skills" / "baja-research" / "SKILL.md"
    ctx.register_skill(
        name="baja-research",
        path=skill_path,
        description="Find and cite verified free academic work for Baja SAE.",
        frontmatter={
            "name": "baja-research",
            "description": "Find and cite verified free academic work for Baja SAE.",
        },
    )
    ctx.on_unload(service.close)


__all__ = ["register", "ResearchService"]
