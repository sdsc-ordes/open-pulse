# GME index reindex runbook

Scripts to **clear and rebuild** the git-metadata-extractor RAG indices —
the per-provider DuckDB stores **and** their Qdrant collections — when an
index restructure (new tables, schema change) requires a from-scratch
re-ingest.

The GME has no built-in "clear" recipe: rebuilding cleanly means deleting
the `.duckdb` file + the provider's Qdrant collections, then running the
provider's `ingest → embed [→ rebuild-qdrant]` recipes
(`python -m src.index.<provider> …`, the same ones the GME `justfile`
exposes). `gme-reindex.sh` wraps both halves.

## ⚠️ Read first

1. **Destructive.** `clear` deletes DuckDB files and Qdrant collections.
   The script is **dry-run by default** — it only acts with `--yes`.
2. **Lock contention (important).** The running GME service holds a
   persistent **read-write** DuckDB handle on every store. The ingest
   CLIs also open the store read-write, so they will hit
   `Conflicting lock is held in PID 0` while the service is up. **Stop the
   extractor (or its request workers) for the duration of a reindex**, e.g.:

   ```bash
   ./scripts/op deploy stop git-metadata-extractor      # or: docker stop git-metadata-extractor
   # … run the reindex against the volume via a one-off container, or restart
   #   the service in an "ingest-only" mode that doesn't hold the handles …
   ```

   (This same lock is why the hub knowledge browser 404/500s on the
   GME-held stores — fixing the GME to serve queries via `read_only`
   connections removes both problems.)
3. **Scale + cost.** A full reindex re-hits every upstream API (GitHub,
   OpenAlex, Zenodo, ORCID, …) and re-embeds via the EPFL RCP endpoint.
   It is an **hours-to-days, rate-limited** job. Do **one provider at a
   time**, watch `status`, and keep `RCP_TOKEN` / provider tokens valid.

## Usage

```bash
cd infra/open-pulse-stack/scripts/gme-reindex

./gme-reindex.sh status                       # Qdrant collections + point counts
./gme-reindex.sh clear   huggingface          # DRY-RUN: show what would be deleted
./gme-reindex.sh clear   huggingface --yes    # actually delete duckdb + qdrant
./gme-reindex.sh reindex huggingface --yes    # ingest → embed (→ rebuild-qdrant)
./gme-reindex.sh full    huggingface --yes    # clear + reindex in one go
./gme-reindex.sh full    all --yes            # everything (long!)
```

Flags:
- `--yes` — execute (omit for dry-run).
- `--scope epfl|switzerland|all` — for scoped providers (openalex, github,
  huggingface, zenodo). Default `all` runs both scopes.

Env overrides: `EXTRACTOR_CONTAINER`, `QDRANT_URL`, `INDEX_ROOT`.

## Provider coverage

| Provider | Reindex steps | Scoped | Status |
| --- | --- | --- | --- |
| openalex | ingest → embed → rebuild-qdrant | epfl/switzerland | ✅ wired |
| github | ingest → embed → rebuild-qdrant | epfl/switzerland | ✅ wired |
| huggingface | discover-orgs → ingest → embed | epfl/switzerland | ✅ wired |
| zenodo | ingest → embed | epfl/switzerland | ✅ wired |
| orcid-epfl | discover → ingest → embed | epfl | ✅ wired |
| orcid-switzerland | discover → ingest → embed | switzerland | ✅ wired |
| renkulab | ingest → embed | — | ✅ wired |
| swissubase | ingest → embed | — | ✅ wired |
| epfl_graph | ingest → embed | — | ✅ wired |
| infoscience | discover → fetch-text → extract-matches → extract-relations → fetch-related → ingest-duckdb → embed | — | ✅ wired (multi-step) |
| ethz | raw ingest (`storage/ingest_raw.py`) | — | ⚠️ **VERIFY** |
| oamonitor | no ingest CLI in justfile | — | ⚠️ **VERIFY** |
| ror | `build` | — | ⚠️ **VERIFY** |
| snsf | loads a `data.snf.ch` dump (no ingest CLI) | — | ⚠️ **VERIFY** |
| communities | `build` (derives from zenodo) | — | ⚠️ **VERIFY** |

The **VERIFY** providers don't follow the standard `ingest → embed`
recipe; their `reindex_*` functions carry the best-known command but must
be confirmed against the GME `justfile` and per-provider docs
(`docs/<provider>-index.md`) before a production run. Run `communities`
**after** `zenodo` (it derives from it).

## Recommended order

1. `status` — snapshot current point counts.
2. Stop the extractor service (lock release).
3. Per provider, smallest first to validate the flow:
   `clear <p> --yes` then `reindex <p> --yes`, checking `status` after each.
4. Re-ingest the big ones (openalex, oamonitor, ror, infoscience) last.
5. Restart the extractor; confirm the hub knowledge browser registers the
   stores again.

## What gets deleted by `clear`

- DuckDB: `${INDEX_ROOT}/<dir>/duckdb/<file>.duckdb` (+ `.wal`, `.wal.broken*`).
- Qdrant: the collections listed for that provider (see `status`).
