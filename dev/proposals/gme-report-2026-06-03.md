# GME field report — findings from an Open Pulse index/RAG enrichment run

**From:** Open Pulse / Hub side
**Date:** 2026-06-03
**GME build under test:** `ghcr.io/imaging-plaza/git-metadata-extractor:develop`
(image revision `13f3b4ff` / `063ddcc`, `EXTRACTOR_WORKERS=1`)

Context: we ran the monolith→split migration, re-ingested ~3.1k GitHub repos +
142 users + 15 orgs, ran `--reembed`, and wired the Hub browser to the split
stores. Along the way we hit a handful of GME-side issues worth your attention.
Severity tags: **[bug]** broken behaviour · **[gap]** missing step · **[data]**
data-quality · **[ok]** confirmation something works.

---

## Status after develop `0b91fde` (re-tested 2026-06-03)

Thanks for the fast turnaround. Verified on the new image:

- **#1 — FIXED ✅** `POST /v2/jobs/<id>/cancel` now returns `200` (was `404`);
  per-request model override landed (`23d9190`).
- **#6 — FIXED ✅** `fetch_by_slug("101060684")` now resolves to
  *"BIORECER – Biological Resources Certifications…"* via the search fallback
  (`889088f`); the grant/ISSN drop on record-communities is in `487cc7f`.
  (Operational note from you: the 25 already-dirty rows persist until a re-ingest
  or the point UPDATE in PR #101.)
- **#7 — REOPENED ⚠️** the guard doesn't cover the real failure mode here — it's
  a deterministic ROR ranking collision, not a rate-limit shortlist. **See §7
  below (rewritten with idle-deploy repro + 4 examples).**

---

## 1. [bug] `agent_runtime="llm"` hangs forever when no LLM provider key is set

On our deploy `PROVIDER=openai` but `OPENAI_API_KEY` is empty (and
`OPENAI_BASE_URL` unset). A `POST /v2/extract` with `agent_runtime:"llm"`:

- never returns — job stays `status:"running"` indefinitely (we polled >5 min),
- emits **no error** in the job record (`error`/`detail` empty, `@graph` = 0),
- **occupies the single worker** (`EXTRACTOR_WORKERS=1`), so it blocks the queue,
- **cannot be cancelled**: `DELETE /v2/jobs/<id>` → `405`,
  `POST /v2/jobs/<id>/cancel` → `404` (no cancel route).

Expected: validate the provider/key at request time (or with a bounded timeout
on the chat call) and **fail fast** with a `provider_error`, instead of hanging.
A job-cancel endpoint would also help operators recover a stuck worker.

Note: RCP (`https://inference-rcp.epfl.ch/v1`) exposes a large OpenAI-compatible
chat catalogue (Qwen3-Instruct, Llama-3.3-70B, Mistral-Small, gpt-oss, …); the
`/v2/extract` body has **no per-request provider/model override**
(`V2ExtractRequest` = `source_url, output_format, agent_runtime,
include_context_summary, include_internal_fields`), so pointing a single run at
RCP currently requires changing the global deploy config.

## 2. [gap] Migration `--apply`/`--reembed` does not republish the `.ro.duckdb` snapshot

`scripts/v2/migrate_monolith_to_split.py --apply [--reembed]` correctly fills the
live `<store>.duckdb` and (with `--reembed`) the Qdrant collection, but it does
**not** publish a fresh `<store>.ro.duckdb` snapshot. The Hub reads the RO
snapshot, so after a migration it kept serving the *stale* (pre-migration) data
— e.g. `huggingface_models.duckdb` had 1034 rows while
`huggingface_models.ro.duckdb` still had 0 (dated from before the run).

We worked around it by calling `src.index._snapshot.publish_snapshot(conn,
live_path)` per store. Suggestion: have the migration (and any path that mutates
a store outside `run_embed_step`) publish the snapshot at the end, the same way
the v2-ingest path does.

## 3. [ok] The `--reembed` fix works

Confirming your fix (commit `2558cb3`, "make migration embed actually populate
Qdrant"): `--reembed` drops the `chunks` table + the Qdrant collection and
rebuilds cleanly. For the 4 HF providers it produced
models=1033, datasets=323, organizations=140, spaces=70 points (vs 0 before).
The earlier `--embed`-only path was indeed a no-op because the copied rows
already had `chunks` bookkeeping so `stream_unembedded` saw nothing to do.

## 4. [gap] HuggingFace split stores were empty in the canonical path

The dry-run showed the canonical develop HF stores at **0 rows**
(`huggingface_models/datasets/organizations/spaces`) while the orphaned monolith
`huggingface.duckdb` held 1034/324/153/70. Our earlier rc1 reindex had populated
the *old* paths/collections (`hf_models` etc.), which the org-rename split
(#95/#96) left orphaned. So a fresh develop deploy that only ran the rc1 reindex
serves empty HF until the migration runs. Worth calling out in the migration
runbook (and maybe bootstrapping empty split stores on deploy).

## 5. [gap] Migration doesn't cover communities; split `zenodo_communities` store is unbuilt

`src/index/zenodo_communities/` exists (cli/build/ingest) but its DuckDB store is
not created — the populated store is the legacy `index/communities/communities.duckdb`
(469 rows, all `source='zenodo'`). The monolith→split migration `PLAN` covers
github/hf/zenodo_records but not communities, so the split `zenodo_communities`
provider stays empty. Either migrate `communities` → `zenodo_communities`, or
document that `communities` remains the live store.

## 6. [data] `zenodo_records.primary_community_id` carries non-community ids

A third of distinct `primary_community_id` values are not Zenodo communities —
they're EU grant numbers (`101007165`), ISSNs (`1807-1260`), etc., which 404 on
`GET /api/communities/<slug>`. Of 696 distinct ids referenced by records, only
~464 resolve to real communities. Looks like the zenodo records ingest is
writing a non-community identifier into that column for some records.

## 7. [bug] ROR resolution picks token-coincidental orgs — NOT a rate-limit artifact

**Updated 2026-06-03 after re-testing on develop `0b91fde` (idle deploy).** The
`ownedBy` → ROR mapping resolves many GitHub orgs to unrelated ROR records
(138 of 213 owner→ROR pairs share zero significant tokens). The headline case was
`Edinburgh-Genome-Foundry → "Jøtul (Norway)"` (×18 repos), not `google-deepmind`
(which resolves fine).

**The earlier "ROR rate-limiting → partial shortlist → coincidental match"
hypothesis does not hold for these.** Re-running on an idle deploy with the full
ROR result set reproduces the mismatch deterministically, and a *fresh* ROR query
shows the bad org is the **#1 relevance hit**:

| GitHub org (query) | ROR #1 returned | results | shared tokens |
| --- | --- | ---: | --- |
| Edinburgh Genome **Foundry** | `042epp307` **Jøtul (Norway)** / "Kværner *Foundry*" | 77 | **none** |
| Oxford Protein Informatics Group | `00z4w4f29` **Oxford Research Group** | 1519 | oxford |
| Google Cloud Platform | `04d06q394` **Google (Canada)** | 72 | google |
| InstaDeep | (no result) — GME still stored `Celagix Res. Ltd` | **0** | none |

Root cause: ROR's full-text relevance ranking surfaces a token-coincidental org
as the top hit (Jøtul via the alias "Foundry"; "Oxford Research Group" for any
"Oxford …"), and the resolver **accepts the #1 hit without a name-match check**.
For orgs with no ROR record at all (InstaDeep → 0 results) it still emits a match.

So the shipped `889088f` guard (abstain when the authoritative query *failed*) is
necessary but **insufficient** — here the query *succeeds* and returns a bad #1.
Suggested addition: a **name-similarity threshold** — only accept the ROR hit when
its (display name + aliases) share a distinctive token with the query org name,
and abstain on 0-result / 0-overlap. That single check covers all four rows above.

Full per-owner table attached separately (`gme7_ror_resolutions.txt`). Caveat:
acronym/concatenated handles (`broadinstitute`→Broad Institute,
`usnistgov`→NIST) look token-disjoint to a naive tokenizer but are *correct* — a
good threshold needs alias + acronym awareness, not raw token overlap.

(Unrelated: `rule_based` leaves `pulse:discipline` empty — Wikidata disciplines
only appear under `agent_runtime:"llm"`; worth documenting as llm-only.)

## 8. [minor] Published image omits `scripts/`

`scripts/v2/migrate_monolith_to_split.py` is in the git tree but **not copied
into the `:develop` image** (`/app/scripts/...` absent; only `src/` ships). We
had to bind-mount the script from `origin/develop` to run it. Either ship
`scripts/` in the image or document the bind-mount step in the runbook.

## 9. [info] Single-worker contention under concurrent ingest

With `EXTRACTOR_WORKERS=1`, a client that fans out concurrent `/v2/extract` or
`/v2/indices/<p>/ingest` calls (we used 5–6) saturates the one worker and gets a
stream of `[Errno 104] Connection reset by peer` on the poll calls; they retry
and eventually succeed, but throughput doesn't improve and the log is noisy.
Serial (concurrency 1) was both cleaner and slightly faster. Not a bug given the
worker count, but a brief note in the API docs ("match client concurrency to
EXTRACTOR_WORKERS") would save others the discovery. Context: we run 1 worker on
purpose because >1 hit DuckDB RW lock contention ("index module unavailable on
this deployment").

---

### Quick repro pointers
- #1: `POST /v2/extract {"source_url":"https://github.com/google-deepmind/alphafold","agent_runtime":"llm","output_format":"jsonld"}` with `OPENAI_API_KEY` unset → hangs `running`.
- #2: run `migrate_monolith_to_split.py --provider huggingface_models --apply --reembed`, then compare `huggingface_models.duckdb` vs `huggingface_models.ro.duckdb` row counts.
- #6: `SELECT DISTINCT primary_community_id FROM records` → inspect non-URL / grant-number values.

Happy to provide full logs or pair on any of these.
