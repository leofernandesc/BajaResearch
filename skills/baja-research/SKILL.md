---
name: baja-research
description: "Find free, verified, technically relevant academic work for Baja SAE teams."
version: 0.1.0
author: Leonardo Fernandes Cavalcante
license: MIT
metadata:
  hermes:
    tags: [baja-sae, academic-search, engineering, literature]
---

# BAJA Research

Use this skill whenever the user asks for papers, TCCs, monographs, theses,
dissertations, academic literature, technical references, related work, or a
bibliography for a Baja SAE problem. This is an evidence-backed academic
search workflow, not a general web search.

## Non-negotiable policy

- Recommend only records returned by BAJA Research with
  `access_status=verified_pdf`, a `full_text_url`, and anonymous PDF access
  verified by the plugin. A DOI, an open-access label, or a publisher landing
  page alone is not sufficient.
- Every recommendation must match both the requested technical focus and a
  Baja SAE, Mini Baja, Formula SAE/Formula Student, ATV, or off-road vehicle
  context. Do not answer a broad request such as "eletrônica" with generic
  power-electronics papers.
- Prefer TCCs, monographs, dissertations, and theses because they usually give
  the team more implementation detail. Use articles to fill remaining slots
  only when they satisfy the same relevance and free-PDF requirements, unless
  the user explicitly asks to see articles first.
- Exclude electric, hybrid, battery-electric, and fuel-cell vehicle work by
  default. Only include it when the user explicitly asks for that technology.
- Never weaken the free-full-text or Baja-context requirements, even if the
  user asks for more results. Return fewer works and explain the shortage.
- Never scrape Google Scholar and never imply that BAJA Research searched it.

## Required search workflow

1. Identify the technical area, phenomenon or problem, vehicle application,
   and useful academic vocabulary in the user's request.
2. Create a short `technical_focus` that contains the actual subject without
   generic context words. Examples: `suspension geometry optimization`,
   `chassis fatigue finite element analysis`, or
   `telemetry data acquisition sensors CAN`.
3. Build approximately three to five complementary queries. Use English
   technical terminology when useful, but include Portuguese repository terms
   when they can retrieve Brazilian TCCs. Start narrow and broaden carefully:
   Baja SAE or Mini Baja, then Formula SAE/Formula Student, ATV/off-road, and
   finally the relevant vehicle-engineering domain. Do not broaden away from
   the requested technical focus.
4. Call `search_academic_papers` once with the complete query list,
   `technical_focus`, the requested final `limit`, and
   `document_preference=long_form_first`. The limit is the final number of
   recommendations, not the number requested from each source.
5. Use `document_preference=articles_first` only when the user explicitly
   prioritizes articles. Keep `exclude_electric_vehicles=true` unless the user
   explicitly asks for EV, hybrid, battery, or fuel-cell literature.
6. Inspect the returned `policy`, `sources`, `warnings`, `filters_applied`, and
   each record's access fields. Never recover a rejected URL from raw metadata
   or from memory.
7. Use `get_paper` for a known item, `find_related_papers` for follow-up
   discovery, `format_citation` for ABNT/BibTeX, and
   `research_cache_stats` for diagnostics.

## Response format

Answer in the user's language. For chat or WhatsApp, list at most five works
by default, even when the tool found more. For each work, show only source data:

1. exact title;
2. shortened author list;
3. year;
4. document type and institution or venue, when returned;
5. DOI, when returned;
6. the verified free-PDF link from `full_text_url`;
7. one short sentence explaining why it is relevant. Mark that final sentence
   as your interpretation rather than bibliographic metadata.

Do not paste complete abstracts. State how many additional qualifying results
exist using `more_available`. If fewer works than requested survived, say that
the plugin preferred returning fewer results over including off-topic,
paywalled, unverified, or EV material. Briefly disclose every source reported
as unavailable, rate-limited, or degraded.

## Bibliographic truth rules

- Titles, authors, years, document types, institutions, venues, DOI, citation
  counts, and URLs must come from the current tool output. Never fill a missing
  field from memory and never invent a plausible value.
- Treat `sources`, access verification, filters, and API status as source data.
  Treat your explanation of practical relevance as LLM interpretation, and
  phrase it accordingly.
- A missing field means "not returned". Do not infer open access from a DOI,
  repository page, filename, or source label.
- Print `full_text_url` as the access link only when the result says
  `access_status=verified_pdf`. Do not replace it with `url` or a DOI landing
  page.
- If no qualifying work is returned, report that honestly and suggest a more
  precise technical focus or a broader Baja/Formula/off-road formulation. Do
  not substitute unverified references.
- If an academic source fails, use results from healthy sources and identify
  the partial failure. Do not claim that every configured source answered.
