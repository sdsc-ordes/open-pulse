# Proposal: read-only DuckDB snapshots for external consumers

**For:** git-metadata-extractor maintainers
**From:** Open Pulse / Hub side
**Status:** proposal + working proof-of-concept (not pushed to your repo)

## The problem

The GME keeps **one long-lived read-WRITE DuckDB handle per store** open
while it serves searches + ingests. DuckDB's file lock is single-writer:
it allows *N readers* **or** *1 writer*, never both. So any external
**read-only** consumer that opens the live `.duckdb` file gets:

```
IO Error: Could not set lock on file ".../openalex.duckdb":
Conflicting lock is held in PID 0
```

Today this breaks the **Open Pulse Hub knowledge browser**: it opens each
store `read_only=True` to render the row tables, and its startup
schema-sniff fails on every store the GME holds — so most collections
404/500. We confirmed the GME holds a persistent handle on all 12 stores
(`/proc/<pid>/fd`), so it is not a transient race.

This isn't a hub bug — it's the fundamental DuckDB lock model. It needs a
GME-side decision, which is why we're handing it to you rather than
working around it.

## Options we considered

1. **Serve queries via `read_only` connections** (RW only during ingest).
   Cleanest long-term, but a bigger change to the serving layer and the
   one-writer-per-process assumption.
2. **Publish a read-only snapshot file** the consumer reads instead of the
   live store. Small, additive, leaves serving untouched. **This is the
   PoC below.**
3. Operate on copies / external snapshots out-of-band (cron `cp`). Fragile
   (non-atomic, races the writer).

## Proposed solution (option 2) — `.ro.duckdb` snapshots

After each write, copy the store's tables into a sibling
`<name>.ro.duckdb` **through the live writer connection** (`ATTACH` +
`CREATE TABLE … AS SELECT`), then `os.replace` it into place atomically.
The consumer opens that snapshot read-only — a *different file*, no
contention. Vector `chunks` tables are skipped (consumers don't read
them; they dominate size). ~300 MB copies in a few seconds; debounced so
multi-GB stores aren't recopied on every unit ingest.

### Acid test (the failure, reproduced + resolved)

With a live RW connection held open (as your serving layer does), a
**separate process** opens the published snapshot read-only and reads the
rows — no lock conflict — while the live file stays correctly blocked:

```
live writer open; persons=3
publish_snapshot -> {ok: True, tables: 1, ms: 19}   # chunks skipped
RO snapshot OPEN OK while live RW held → persons=3, chunks absent  ✅
CONTROL: live file RO open correctly BLOCKED                       ✅
```

## Working PoC (attached patch — `ro-snapshot.patch`, 3 commits)

Branch `feat/ro-snapshot-for-hub` off `develop`, **kept local** (not
pushed). 11 tests green. Apply with `git am *.patch` or cherry-pick the
ideas:

| File | What |
| --- | --- |
| `src/index/_snapshot.py` (new) | `publish_snapshot(conn, db_path, *, skip_tables={"chunks"})` + `snapshot_path_for` + `publish_snapshot_debounced`. Generic, best-effort (never raises). |
| `src/v2/indices/_embed_step.py` | One hook in `run_embed_step` after the checkpoint → covers all 16 v2-ingest providers from a single place. Toggle `INDEX_DUCKDB_SNAPSHOT`, debounce `INDEX_SNAPSHOT_MIN_INTERVAL_S` (60s default). Status under `summary['snapshot']`. |
| `src/v2/indices/reset.py` | `_delete_duckdb` also drops the `.ro.duckdb` so a reset doesn't leave the consumer serving stale rows. |
| `tests/index/_shared/test_snapshot.py`, `tests/v2/indices/test_embed_step_snapshot.py`, `test_reset_snapshot.py` | Unit + the cross-process acid test. |

### Still to wire if you take this route
- **CLI catalogs** (ror, snsf, infoscience, epfl_graph, communities) +
  **ethz `build()`** — publish a snapshot at the end of each cron ingest
  (the v2-ingest providers are already covered by the central hook).
- **Bootstrap** — publish an empty snapshot on fresh deploy so the
  consumer sees a clean empty state, not a missing file.

## What the consumer (Hub) side needs (already designed, currently reverted)
A one-line change: open `<name>.ro.duckdb` if present, else fall back to
the live file. We'll land that on our side **once you confirm the
approach + the `.ro.duckdb` naming** — we don't want to commit the Hub to
a contract you haven't agreed to.

## Open questions for you
1. Prefer **option 1 (serve read-only)** or **option 2 (snapshots)**? If
   option 1, the Hub change is even simpler (just works) and we can drop
   this entirely.
2. If snapshots: OK with the `<name>.ro.duckdb` naming + skipping
   `chunks`? Per-job publish vs the debounce we sketched?
3. Want us to open a PR with the PoC branch for review, or is the patch
   enough?
