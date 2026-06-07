# Data Enrichment Plan — closing the Crawler ⇄ GME/Oxigraph loop

_2026-06-07 · Carlos Vivar Rios_

## Goal

Increase the **amount and richness** of data in the open-pulse graphs by (a) feeding
what the GME has already resolved into Oxigraph **back into the Neo4j crawler graph**,
and (b) using Oxigraph + the GME reference indices as **new crawl sources** so the
crawler reaches software the GitHub social graph can't.

## Current assets (2026-06-07)

**Neo4j (crawler graph):** 257,289 Repo · 59,635 User · 1,535 RorOrg · 1,138 Org.
Edges: DEPENDS_ON 265k, OWNS 170k, STARRED 77k, CONTRIBUTES_TO 58k, WATCHES 28k,
FOLLOWS 16k, COMMENTED_ON 15k, OPENED_ISSUE 13.5k, OPENED_PR 8.4k, MEMBER_OF 9k,
FORK_OF 4k, REVIEWED_PR 3.4k, AFFILIATED_WITH 3.4k.

**Oxigraph (SPARQL, extracted entity graphs):** big `2026-05/hybrid` (2.1M triples,
~13.8k repos) + per-domain graphs: contributors/protein-ai, pharma-tools,
ruben-laplaza(+network), sdsc-v2, epfl-enac(+3hop), authors/protein-ai. Across all:
~29k Persons, ~11.3k repos, 1,903 ORCID persons, 918 Infoscience persons, 1,850 ROR
orgs, 478 ScholarlyArticles.

**GME reference indices (DuckDB, used for enrichment, NOT yet crawl sources):**
openalex 3.07M · ROR 301k · oamonitor 127k · swissubase 126k · orcid 112k ·
infoscience 96k · zenodo_records 91k · github_repos 45k · epfl_graph 42k ·
renkulab 37k · ethz_research_collection 33k · huggingface ~3k. (snsf,
zenodo_communities, huggingface_users currently empty.)

## What we're missing

1. **Neo4j ↔ Oxigraph silo.** The crawler builds a bare GitHub graph in Neo4j; the
   GME's identity/affiliation/publication resolution lands only in Oxigraph. _(Partly
   fixed — see "Implemented".)_
2. **Cross-platform coverage is thin.** Nearly all crawls used GitHub-v1. GitLab /
   Renku / Zenodo / Infoscience / DataCite presence is largely absent. The big indices
   are barely used as crawl *sources*.
3. **No publication/dataset/funding graph.** Zenodo/DataCite works link to GitHub
   repos; openalex/SNSF hold co-authorship + grants. The crawler only walks GitHub's
   social graph, so software↔paper↔grant↔funder links are missing.
4. **Sparse org membership.** GitHub `MEMBER_OF` only 9k; an org's real people
   (via ROR + openalex affiliations) aren't represented.
5. **Identity not unified.** A person across GitHub + ORCID + Infoscience + openalex
   isn't a single node (only partially reconciled by the GME).

## The enrichment loop (Oxigraph / indices → crawler)

- **Reverse-seed from publications/datasets** — query Zenodo/DataCite/openalex for the
  GitHub URLs of research software, feed as **v2 crawl seeds**. Reaches software the
  follow/contributor graph can't. _(Highest data gain.)_
- **Seed from ORCID/Infoscience persons** — for researchers already in Oxigraph,
  resolve their GitHub and crawl it.
- **ROR-driven org expansion** — use ROR + openalex affiliations to enumerate an org's
  real members beyond sparse GitHub membership.
- **Frontier prioritization** — score discovered repos as "research software" vs noise
  using the indices before deep-crawling.
- **Continuous backfill** — sync GME enrichment (ORCID/Infoscience/ROR affiliation)
  from Oxigraph into Neo4j after every quest so the two stores never drift.

## Implemented (2026-06-07)

**Oxigraph → Neo4j identity/affiliation backfill** (`/tmp/backfill2.py`): join
`pulse:githubUsername` → `User.login` (normalising bare handles to full URLs), set
`User.orcid` / `User.infoscience`, and MERGE `(:User)-[:AFFILIATED_WITH]->(:RorOrg)`.

Results: AFFILIATED_WITH **373 → 3,405** (9×), Users w/ ORCID **826 → 1,909**,
Users w/ Infoscience **~0 → 746**. Top orgs now queryable in Neo4j: EPFL (250),
ETH Zurich (43), MIT (33), Genentech (31), Stanford (31), Berkeley, Cambridge, Harvard.

## Roadmap (prioritised)

1. **Reverse-seed quest** — extract GitHub URLs from Zenodo/DataCite/openalex (per
   domain) → v2 crawl → publication-linked software. _Biggest gain._
2. **Backfill as a pipeline step** — run the Oxigraph→Neo4j sync automatically after
   each quest (productionise `backfill2.py`).
3. **v2 cross-platform seeding** — Zenodo communities / Renku groups / GitLab orgs as
   direct seeds to bring in non-GitHub node types.
4. **ROR org-membership expansion** — openalex affiliation → org members.
5. **Identity unification** — single Person node keyed across GitHub/ORCID/Infoscience.

## Notes / caveats

- `keywords` and `publications` are sparse for research-IT tooling regardless of
  GME runtime; hybrid's main win is **ROR/ORCID org-identity resolution**, not keywords.
- v2 crawler github-org expansion works but is slower than v1; its real value is the
  non-github seed types.
