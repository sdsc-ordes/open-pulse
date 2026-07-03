---
title: Activity Tracking
slug: /operations/activity-tracking
---

# Activity Tracking

`open-pulse activity sync` polls GitHub for repository **push activity**
and appends a weekly, date-partitioned JSONL change log. It's built to run
weekly across the whole indexed corpus (tens of thousands of repos) without
burning the API rate limit — it never issues one request per repo.

## How it stays cheap

1. Reads the owner list from the `github_repos` index (iterates **owners**,
   not every repo).
2. For each owner, lists repos sorted by `pushed` descending and
   **early-stops** once a repo's `pushed_at` falls at/under the watermark —
   so a list call costs *O(movers)*, not *O(repos)*.
3. Conditional requests (`If-None-Match`): an unchanged owner returns
   `304`, which **does not count against the rate limit**.
4. Only the movers get a `/repos/{owner}/{repo}/activity` fetch; their
   `push` / `force_push` events are appended to the weekly JSONL.

At ~1%/week churn, a full run is a handful of real requests plus a sweep of
free `304`s.

## Usage

```bash
open-pulse activity sync
```

The GitHub token is resolved from `GITHUB_API_TOKEN`, then
`CRAWLER_GITHUB_TOKEN`, then `GITHUB_TOKEN` (or `--token`).

| Flag | Default | Purpose |
| --- | --- | --- |
| `--index` | `data/index/github_repos/duckdb/github_repos.ro.duckdb` | Repo list source. |
| `--out` | `data/activity` | Output dir (weekly JSONL + `state.json`). |
| `--window-days` | `7` | First-run lookback when there is no state. |
| `--owner` / `--limit-owners` | — | Restrict scope (debugging). |
| `--dry-run` | off | Detect movers without fetching or writing. |

## Output

- `data/activity/activity-<ISO-year>-W<week>.jsonl` — one push event per
  line (`repo`, `activity_type`, `ref`, `before`/`after` SHAs, `timestamp`,
  `actor`).
- `data/activity/state.json` — per-owner `{kind, etag}` and the run
  watermark, so the next run's unchanged owners are free.

Run it weekly from the CLI orchestrator container (cron or a pipeline
step). The `after` SHA + timestamp are exactly the signal to trigger a
re-crawl / re-extract of just the repos that moved.
