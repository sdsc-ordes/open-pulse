# How To Integrate Mordred Extraction to OpenSearchDashboards

This guide describes how the current Grimoire Docker Compose setup behaves.

For any interactions with GitHub, copy `.env.dist` to `.env` in this folder and set `GITHUB_API_TOKEN` there, along with the other GrimoireLab credentials used by Docker Compose and the generated Mordred `setup.cfg`.

## Launch Grimoire

Checklist before launching:

- credentials are configured (or default are being used)
- `.env` has been filled from `.env.dist` with the required GitHub and optional GitLab tokens
- all projects that need to be extracted are in `config/projects.json` under the right categories

For exposing the OpenSearch Dashboards UI on a specific port, configure the `nginx` ports in `docker-compose.yml`.

Start the stack from this folder:

```bash
docker compose up -d
```

## What Docker Compose Does

When you run `docker compose up -d`, the compose file starts the main services plus two helper services:

- `prepare-grimoire-config`
- `prepare-opensearch`

### `prepare-grimoire-config`

This one-shot container runs first and:

- reads `.env`
- renders `config/setup.cfg.template` into a concrete `setup.cfg`
- copies `config/projects.json`
- writes both files into the shared `grimoire-conf` Docker volume

`mordred` then mounts that generated config from `/home/grimoire/conf`.

### `prepare-opensearch`

This one-shot container waits for:

- the OpenSearch API
- the OpenSearch Dashboards API

Then it executes every shell script in `scripts/` in filename order, except the host-only scripts that are explicitly skipped.

At the moment, the compose-driven setup path uses the shell scripts under `scripts/`, not the Python equivalents under `python-scripts/`.

The scripts in `scripts/` are used for:

- creating aliases
- creating index patterns
- uploading dashboard NDJSON files

Because these scripts are executed in filename order, failures in an earlier script can prevent later setup steps from running.

## What Docker Compose Does Not Do

`docker compose up -d` does not install any host cronjob.

The project watcher for `projects.json` is a host-side script and must be installed manually. This is separate from the container startup flow.

## Manual Cronjob Installation

To install the watcher cronjob on the host, run one of these manually:

```bash
bash ./scripts/install_intake_cronjob.sh
```

or:

```bash
python3 ./python-scripts/install_intake_cronjob.py
```

These commands create a host cron entry that periodically runs the watcher script which:

- optionally performs `git pull`
- checks whether `config/projects.json` changed
- restarts the `mordred` service if it did

The cron schedule is controlled by `GRIMOIRE_INTAKE_CRON_SCHEDULE` in `.env`, and the watcher can optionally `git pull` before checking `projects.json` via `GRIMOIRE_ENABLE_GIT_PULL=true`.

To confirm the cronjob was installed:

```bash
crontab -l
```

## Dashboards and SIGIL Setup

The compose startup path runs the setup scripts automatically through `prepare-opensearch`.

You can also run individual scripts directly if you need to retry a step:

- `./scripts/make_index_patterns.sh`
- `./scripts/upload_sigils_to_opensearch.sh`
- `./scripts/create_opensearch_aliases.sh`

Python equivalents also exist under `python-scripts/`.

[All SIGIL dashboards can be found here](https://github.com/chaoss/grimoirelab-sigils/tree/main/panels/json/opensearch_dashboards).

If there are issues in the GitHub PR and Issues dashboards with fields like Submitters then the relevant fix is tracked here: [chaoss/grimoirelab-sigils#517](https://github.com/chaoss/grimoirelab-sigils/issues/517)

## Updating Projects

1. Edit `config/projects.json`.
2. Restart `mordred` manually with `docker compose restart mordred`, or let the manually installed cronjob detect the change.
3. Refresh OpenSearch Dashboards.

> **Note:** depending on how the stack was started, the Mordred container may be named `open-pulse-mordred` and **not** be a service of the `open-pulse-stack` compose project. In that case `docker compose restart mordred` fails with `no such service: mordred` — restart it by container name instead: `docker restart open-pulse-mordred`.

## PR review/merge metrics (`cr_*`): wire the `github:pull` backend

The CHAOSS `cr_*` metrics (`cr_reviews`, `cr_accepted`, `cr_declined`, `cr_duration`, `pr_time_to_close`, self-merge) need GitHub **PR merge/review** fields (`merged`, `merge_author_login`, `num_review_comments`, `pull_reviews`). The plain `[github]` backend collects PRs only as *issues* (default `category = issue`) — those documents land in `github_demo_enriched` with **none** of those fields, so the `cr_*` metrics read empty.

`setup.cfg` already defines a `[github:pull]` backend (`category = pull_request`, `enriched_index = github-pull_enriched`), but it is **idle unless a project routes repos to it**. To enable PR metrics for a project, add the repo URLs under a `"github:pull"` key in `projects.json` alongside `git`/`github`:

```jsonc
"my-project": {
  "meta": { "title": "My Project" },
  "git":          [ "https://github.com/owner/repo.git", ... ],
  "github":       [ "https://github.com/owner/repo",     ... ],
  "github:pull":  [ "https://github.com/owner/repo",     ... ]   // ← enables cr_*
}
```

Then restart Mordred. PR data is collected into `github-pull_raw` → enriched into `github-pull_enriched`. The hub's CHAOSS API queries the `github_*_enriched` wildcard, which already matches `github-pull_enriched`, so no metrics-code change is needed.

Caveats:

- PR collection is rate-limited (GitHub PR API) and slow for large repo sets.
- A PR then exists in **both** `github_demo_enriched` (issue copy, no `merged` mapping) and `github-pull_enriched` (pull copy, proper `merged`/review fields). Querying the `github_*_enriched` **wildcard** for PR/merge data is therefore wrong twice over: it double-counts each PR, and a `merged` term across the wildcard silently matches **zero** documents (mapping clash). For this reason the hub's PR/merge metrics (`cr_accepted`, `cr_declined`, `cr_duration`, `pr_time_to_close`, `cr_reviews`, `self_merge`, `closure_ratio`) query **`github-pull_enriched` directly**, not the wildcard. Issue metrics (`issues_*`, `first_response`, `issue_response_time`) keep the wildcard. Field names on the pull docs: `merged` (bool), `merged_at`, `closed_at`, `num_review_comments`, `merge_author_login` (note: **not** `merge_date` / `*_without_bot`).

## Utils for debugging

### List data sources / index patterns

Sanity check. It can also be seen on OpenSearch UI under Dashboard Management - Index patterns.

```bash
curl -u admin:YOUR_PASSWORD -X GET "http://localhost:5601/api/saved_objects/_find?type=index-pattern" \
  -H "osd-xsrf: true"
```

### Check the indexes and aliases

This allows to confirm the Mordred extraction went well for all your projects in projects.json. You can also confirm by seeing `collection finished` in the Mordred docker logs.

It can also be seen on OpenSearch UI under Index Management - Indexes.

```bash
curl -k -u admin:YOUR_PASSWORD -X GET "https://localhost:9200/_cat/indices?s=i"
```

```bash
curl -k -u admin:YOUR_PASSWORD -X GET "https://localhost:9200/_cat/aliases?v"
```

(ignore check of certificates with `-k`)

### Get the dashboards

It can also be seen on OpenSearch UI under Dashbaords Management - Saved Objects.

```bash
curl -u admin:YOUR_PASSWORD -X GET "http://localhost:5601/api/saved_objects/_find?type=dashboard" \
  -H "osd-xsrf: true"
```

### Enrichment stalls at 0% CPU (SortingHat name-unify loop)

**Symptom:** after `collection phase finished`, Mordred goes silent for a long time at **0% CPU** (no new log lines, `sleep_for` cycle far exceeded). Enriched indexes (`github-pull_enriched`, etc.) stop growing. The process is blocked in the **identities phase**, waiting on a SortingHat background job that never frees the worker.

**Cause:** the identities phase enqueues a `unify` job by **name** that, over a large individual pool (many repos × contributors), takes ~10–11 min per pass, merges 0 individuals, and immediately re-runs — saturating the single `sortinghat_worker`. Mordred's `do_affiliate` then blocks (and, on a GrimoireLab bug, its error handler raises `UnboundLocalError: ... 'job_id'` at `sortinghat_gelk.py:189`). The loop ignores the `[sortinghat] matching` setting, is **not** a SortingHat `ScheduledTask` nor an rq-scheduler job, and regenerates with the same deterministic RQ job id if deleted.

**Diagnose:**

```bash
# Mordred idle? compare last log time to now; CPU should be ~0 when hung
docker logs --tail 1 open-pulse-mordred 2>&1 | grep -oE '^[0-9-]+ [0-9:]+'
docker stats --no-stream --format 'CPU={{.CPUPerc}}' open-pulse-mordred
# the offending worker job: a recurring unify(['name'], ...)
docker logs --tail 5 $(docker ps --format '{{.Names}}' | grep sortinghat_worker) 2>&1 | grep unify
```

**Fix (reversible):** disable the identities phase so enrichment runs straight after collection. In `config/setup.cfg`:

```ini
[phases]
collection = true
identities = false   # was true — stops the name-unify loop that jams the worker
enrichment = true
panels = false
```

Then restart Mordred (`docker restart open-pulse-mordred`). The name-unify loop stops at the source and enrichment proceeds. `cr_*` and other metrics still read existing identities resolved synchronously during enrichment; only new unify/affiliate merging is skipped. **Re-enable `identities = true` once the recurring name-unify is fixed upstream** (tracked: GrimoireLab `do_affiliate` `UnboundLocalError`, and the identities phase ignoring `matching`).
