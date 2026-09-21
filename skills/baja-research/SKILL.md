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
4. For a broad domain request such as “eletrônica”, preserve the Baja
   application in at least one query (for example, Baja SAE vehicle
   electronics, off-road telemetry, or Formula SAE embedded systems). Do not
   return generic power-electronics papers as the primary answer merely
   because they match the broad word.
5. When the user asks for detail, a monograph, TCC, thesis, dissertation, or
   extensive work—or when the topic is broad—include a thesis/repository
   query, such as `undergraduate thesis`, `dissertation`, or `institutional
   repository`. The tool defaults to `prefer_theses=true` and adds a small
   retrieval safety net when the LLM omitted that variant.
6. Call `search_academic_papers` with the complete query list. Its `limit` is
   the final number of records, not the per-source request size. Keep
   `baja_context=true` unless the user explicitly requests a domain-only
   search, and keep `prefer_theses=true` for detailed work.
7. Use `get_paper` for detail, `find_related_papers` for follow-up discovery,
   and `format_citation` when the user requests ABNT or BibTeX.
8. Present no more than five papers by default. For each, include the exact
   returned title, shortened author list, year, venue when available, DOI or
   verified URL, and a short explanation of relevance. Do not paste full
   abstracts. If the tool reports `invalid` or `unknown` link verification,
   do not print that raw URL; say that no verified access link was returned.
9. Answer in the user's language and mention that the user can ask for more
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
- A DOI is a bibliographic identifier returned by a source; only expose a
  publisher/repository URL when the tool marks it as verified. Never copy a
  stale or unverified URL from a raw source payload.
- If one source is rate-limited or unavailable, use the remaining results and
  briefly disclose the partial availability. Do not imply that all three
  sources answered.
- If no paper is returned, say that the configured sources did not find a
  usable result and suggest a query refinement; do not answer with invented
  references.
