---
name: baja-research
description: "Search and cite real academic papers for any Baja SAE engineering or project-management question."
version: 0.1.0
author: Leonardo Fernandes Cavalcante
license: MIT
metadata:
  hermes:
    tags: [baja-sae, academic-search, engineering, literature]
---

# BAJA Research

Use the BAJA Research tools whenever the user asks for papers, academic
articles, literature, references, related work, or technical studies. The
plugin searches OpenAlex, Semantic Scholar, and Crossref and returns normalized
records. It is not a general web search tool.

## Required workflow

1. Understand the user's engineering or management problem and identify its
   domain, phenomenon, application, and useful technical vocabulary.
2. When helpful, translate the terminology to technical English. Keep the
   original language in the final answer.
3. Build about three to five complementary queries: start with Baja SAE when
   relevant, broaden to Formula SAE/Formula Student/off-road/ATV/automotive
   engineering, and include the general technical domain. Do not treat this
   list as a rigid taxonomy; structures, materials, manufacturing, welding,
   suspension, dynamics, brakes, powertrain, CVT, ergonomics, safety,
   embedded systems, data acquisition, telemetry, sensors, CAN, project
   management, optimization, and testing are all valid examples.
4. Call `search_academic_papers` with the complete query list. Its `limit` is
   the final number of records, not the per-source request size.
5. Use `get_paper` for detail, `find_related_papers` for follow-up discovery,
   and `format_citation` when the user requests ABNT or BibTeX.
6. Present no more than five papers by default. For each, include the exact
   returned title, shortened author list, year, venue when available, DOI or
   URL, and a short explanation of relevance. Do not paste full abstracts.
7. Answer in the user's language and mention that the user can ask for more
   detail, related papers, date/open-access filters, a broader search, or a
   citation.

## Bibliographic truth rules

- Titles, authors, years, venues, DOI, citation counts, URLs, and open-access
  claims must come from tool output. Never fill them from memory.
- Never invent a plausible DOI, citation count, author, URL, or paper.
- Treat `source`/`sources`, `source_scores`, and API status as data from the
  tools. A relevance explanation is an LLM interpretation and should be
  phrased as such.
- If a field is absent, say it was not returned. Do not infer that a paper is
  open access merely because a DOI or publisher page exists.
- If one source is rate-limited or unavailable, use the remaining results and
  briefly disclose the partial availability. Do not imply that all three
  sources answered.
- If no paper is returned, say that the configured sources did not find a
  usable result and suggest a query refinement; do not answer with invented
  references.
