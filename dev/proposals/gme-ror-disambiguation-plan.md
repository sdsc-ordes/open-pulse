# Proposal: evidence-based ROR disambiguation (fixing the #7 residual)

**For:** git-metadata-extractor maintainers
**From:** Open Pulse / Hub side
**Builds on:** the `_ror_match_has_nexus` guard (PR #103) — keep it, extend around it.

## Where we are

The distinctive-token guard fixed the worst class (`Edinburgh-Genome-Foundry →
Jøtul` via the generic alias "Foundry" → now rejected ✅). But a residual
remains, confirmed by unit-testing the guard on the deploy:

| handle | accepted ROR | why it slips through |
| --- | --- | --- |
| GoogleCloudPlatform | Google DeepMind | shares distinctive token "google" |
| oxpig | Oxford Research Group | shares "oxford" (but oxpig = Oxford **Protein Informatics** Group) |

The root limitation: **the resolver decides from the org *name* alone, and takes
ROR's #1 relevance hit.** A name token can't disambiguate *which* Google / which
Oxford org. The fix is to (a) decide over a **candidate shortlist**, not the #1,
and (b) bring in **evidence beyond the name** — and yes, use an **agent** to
adjudicate when the deterministic evidence runs out.

## Core redesign: resolution = evidence-based selection over a shortlist

Replace "query ROR → take #1 → guard" with a tiered resolver that returns
`{ror_id | null, method, confidence, alternatives}`:

```
1. SHORTLIST   ROR free-text search → top-K (5–10) candidates, full metadata
               (names, aliases, acronyms, country, type, locations, LINKS/homepage,
                wikipedia, external_ids, relationships).

2. ORG EVIDENCE collect signals about the GitHub org (cheap, already fetched):
               - org homepage / blog URL  → domain
               - org `email` domain, verified domains
               - location, display name, bio/description
               - a few repo homepages / README "affiliation" lines

3. TIER A — deterministic, high precision (no LLM). Decide if any fires:
   A1 DOMAIN MATCH   org homepage domain == a candidate's ROR `links` domain
                     → accept that candidate. (This alone fixes the table above:
                      GoogleCloudPlatform→cloud.google.com ≠ DeepMind's domain;
                      oxpig→opig.stats.ox.ac.uk ≠ oxfordresearchgroup.org.uk.)
   A2 EXTERNAL ID    shared Wikidata/GRID/ISNI/Crossref-funder id
   A3 EXACT NAME     normalized exact name/acronym equality

4. TIER B — prune the shortlist with the existing `_ror_match_has_nexus`
            (drops the Jøtul-class generic-overlap candidates).

5. TIER C — AGENT adjudication, ONLY when ≥2 candidates survive B and no Tier-A
            signal fired. Give the LLM the org evidence + the surviving
            candidates (name/aliases/country/type/homepage/description) and ask
            it to pick the single best id OR return "none". Structured output:
            {ror_id | null, confidence: 0–1, reason}.

6. ABSTAIN  no Tier-A hit, 0 candidates survive B, or the agent returns "none"
            / low confidence → emit NO ROR (standalone), as the guard does today.
```

## Why agent-as-adjudicator, not agent-only

- **Precision & cost:** a homepage-domain match is more trustworthy than an LLM
  guess *and* free. Most ambiguity (Google/Oxford/Novartis-country) is resolved
  at Tier A. The agent only fires on the genuinely-ambiguous tail → bounded cost.
- **Hallucination guard:** the agent chooses from a *closed* shortlist (can't
  invent an org) and may answer "none" → abstention stays first-class.
- **A small model suffices** (the RCP catalogue has Qwen3-Instruct / Mistral-Small
  class models); this is a constrained multiple-choice + abstain task, not
  open generation.

## Signals, ranked by precision (use in this order)

1. homepage/website domain match  (≈ decisive)
2. shared external id (Wikidata/GRID/ISNI/funder DOI)
3. acronym exact match
4. distinctive shared name token + country/location agreement
5. agent judgment over the shortlist  (tie-breaker / last resort)

## Provenance & confidence (store it)

Emit `pulse:ownedBy` with the **method** (`domain` / `external_id` / `exact` /
`token+geo` / `agent` / `none`) and a confidence, plus the runner-up ids. Lets
downstream trust-weight, and makes regressions auditable.

## Validation harness (before shipping)

Build a gold set: the 138 reported suspect pairs (expected: reject/abstain or the
*correct* ROR) + a set of known-correct resolutions (broadinstitute→Broad,
huggingface→Hugging Face, etc.). Measure per tier: **precision, abstain-rate,
and "correct-when-decided"**. Ship in **shadow mode** first (log new vs current
decision per repo, no write) to quantify the delta on real traffic.

## Incremental path (each step ships value alone)

1. **Tier A1 (domain match) only** — biggest precision win, pure deterministic,
   no LLM. Likely resolves most of the residual on its own.
2. Add Tier A2/A3 + provenance/confidence fields.
3. Add Tier C agent for the remaining ambiguous tail, behind a flag, shadow-mode.
4. Backfill: re-resolve the affected orgs (needs a cache-bust — see note) and
   re-emit `ownedBy`.

## Operational note for the backfill

Re-resolution won't show until the provider/pipeline cache is bypassed — on the
current deploy a re-extract returns the cached (pre-fix) `ownedBy`. A
per-request `refresh`/`no_cache` flag on `/v2/extract` (mirroring the ingest
`refresh`) would let consumers force a clean re-resolve for targeted backfills.
