---
title: CHAOSS Metrics API
slug: /reference/chaoss-api
---

# CHAOSS Metrics API

The [Hub](../hub/index.md) exposes its 35 CHAOSS community-health
metrics as a JSON REST API — the same computations the dashboard runs,
scriptable from notebooks and external services. Metrics are grouped in
three buckets: **Community**, **Popularity** and **Quality**.

Base URL: your Hub instance — `https://openpulse.epfl.ch` on the EPFL
node, `http://localhost:<HUB_PORT>` locally.

## Authentication

Hub credentials over HTTP Basic auth (the read-only
`HUB_AUTH_READER` password is enough; the username is free-form):

```bash
curl -su "reader:$HUB_AUTH_READER" \
  "https://openpulse.epfl.ch/api/v1/metrics/chaoss"
```

Deployments with `HUB_PUBLIC_KNOWLEDGE=true` serve these endpoints
without auth.

## Endpoints

### Catalogue (static, no store access)

| Endpoint | Returns |
| --- | --- |
| `GET /api/v1/metrics/chaoss` | Every metric spec; filter with `?category=Community\|Popularity\|Quality`. |
| `GET /api/v1/metrics/chaoss/topics` | The three buckets with metric counts. |
| `GET /api/v1/metrics/chaoss/metrics/{slug}` | One metric spec (404 if unknown). |

### Per-repository (computed live)

| Endpoint | Returns |
| --- | --- |
| `GET /api/v1/metrics/chaoss/repositories/github.com/{owner}/{repo}/metrics` | All metrics for one repo. |
| `GET /api/v1/metrics/chaoss/repositories/github.com/{owner}/{repo}/metrics/{slug}` | One metric for one repo. |

### Per-project (GrimoireLab project = named set of repos)

| Endpoint | Returns |
| --- | --- |
| `GET /api/v1/metrics/chaoss/projects` | Project list from the active `projects.json`. |
| `GET /api/v1/metrics/chaoss/projects/{project}/metrics` | All metrics rolled up across the project's repos (additive counts are summed, ratios averaged; people counts are summed without cross-repo dedup and flagged `approx`). |
| `GET /api/v1/metrics/chaoss/projects/{project}/metrics/{slug}` | One metric with the per-repo breakdown. |
| `GET /api/v1/metrics/chaoss/projects/{project}/repositories` | The repos in the project. |

## Query parameters

| Param | Applies to | Meaning |
| --- | --- | --- |
| `window` | repo + project endpoints | Look-back window in days, 7–3650. Default 3650 (≈ all-time). |
| `category` | catalogue + repo endpoints | Compute only one bucket. |
| `include` | repo endpoints | Comma-separated heavy optional fields: `traces` (the exact upstream queries + per-store errors), `recipes` (reproducibility scripts), `series` (time series). All omitted by default to keep payloads small. |
| `refresh` | project endpoints | `true` bypasses the project-metric cache and recomputes. |

## Response shape

Each metric in a response carries `slug`, `category`, a display
`value` + `label`, an optional `secondary` breakdown, and — when
requested via `include` — `series`, `traces` and `recipes`. A metric
whose upstream store fails reports `value: "—"` with the error in its
trace rather than failing the whole request.

```bash
# Contributor count for one repo, with the time series
curl -su "reader:$HUB_AUTH_READER" \
  "https://openpulse.epfl.ch/api/v1/metrics/chaoss/repositories/github.com/sdsc-ordes/gimie/metrics/contributors?include=series"
```

Per-project results are cached (default TTL 8 days, disk-persisted) and
warmed weekly — see
[Metrics & CHAOSS](../concepts/metrics-and-chaoss.md) for the caching
model.
