---
title: Metrics & CHAOSS
slug: /concepts/metrics-and-chaoss
---

# Metrics & CHAOSS

Open Pulse turns raw git and GitHub activity into a stream of
[CHAOSS](https://chaoss.community/) metrics — community health
indicators that go beyond stars and citations and capture how a project
actually behaves over time. The full
[GrimoireLab](https://chaoss.github.io/grimoirelab/) stack runs as part
of the default deployment; this page is a tour of what is wired up and
how to query it.

## What is CHAOSS?

CHAOSS (Community Health Analytics in Open Source Software) is a Linux
Foundation project that publishes a catalogue of metrics, metrics models
and reference implementations for measuring open-source community
health. The catalogue covers four focus areas:

- **Project activity** — commit frequency, PR velocity, issue activity,
  release cadence.
- **Responsiveness** — issue resolution time, PR review time, first
  response time.
- **Diversity & inclusion** — contributor count, organisational
  diversity, new-contributor rate, bus factor.
- **Risk & sustainability** — dependency health, license compliance,
  project age and stability.

The reference implementation is GrimoireLab, which Open Pulse runs in
full.

## The Open Pulse metrics stack

```mermaid
flowchart LR
  subgraph collect [Collect]
    GH[GitHub API]
    GIT[Git repos]
  end

  subgraph orchestrate [Orchestrate]
    M[Mordred<br/>open-pulse-mordred]
    P[Perceval<br/>collectors]
  end

  subgraph store [Store]
    OS[(OpenSearch<br/>raw + enriched)]
    SH[(SortingHat<br/>identities)]
  end

  subgraph apply [Configure]
    A[Projects applier<br/>localhost:1235]
  end

  subgraph view [View]
    D[OpenSearch Dashboards<br/>localhost:5601 / 7508]
    PY[Python notebooks]
  end

  GH --> P
  GIT --> P
  M --> P
  P --> OS
  P --> SH
  A -. projects.json .-> M
  OS --> D
  OS --> PY
```

Every box in that diagram is a real container shipped by
`docker-compose.grimoirelab.yml` (the opt-in overlay enabled by
`op deploy up --with-grimoire`).

### Components

| Component             | Role                                                                                          | Endpoint (host)                            |
| --------------------- | --------------------------------------------------------------------------------------------- | ------------------------------------------ |
| `open-pulse-mordred`  | Orchestrator. Reads a `projects.json`, drives Perceval collectors on a schedule, ships docs to OpenSearch. | (no HTTP surface)                          |
| `opensearch-node1`    | Storage for raw + enriched documents.                                                         | `https://localhost:9200`                   |
| `opensearch-dashboards` | Browse + dashboard editor.                                                                  | `http://localhost:5601`                    |
| `nginx`               | TLS-terminating reverse proxy for the dashboards.                                              | `http://localhost:7508`                    |
| `sortinghat` + `sortinghat_worker` | Author identity unification (one person, many emails).                          | (internal, REST + worker)                  |
| `mariadb`             | Backing store for SortingHat + Mordred state.                                                 | (internal)                                 |
| `valkey`              | Queue between SortingHat and its worker.                                                      | (internal)                                 |
| `projects-applier`    | Receives a fresh `projects.json` over HTTP and atomically swaps it into Mordred.              | `http://localhost:1235`                    |

### The indices

What lands in OpenSearch (document volumes depend entirely on each
node's tracked repositories — `GET /_cat/indices?v` shows yours):

| Index                         | What it holds                                  |
| ----------------------------- | ---------------------------------------------- |
| `git_demo_raw`                | Raw git log events from every tracked repo     |
| `git_demo_enriched`           | Per-commit enriched docs with 181 CHAOSS fields|
| `git-aoc_demo_enriched`       | Areas of code (touched-file aggregation)       |
| `git-onion_demo_enriched_*`   | "Onion model" tiers (core / regular / casual)  |
| `github_demo_*`               | GitHub issue/PR activity                       |
| `github-pull_*`               | PR review/merge events (feeds the `cr_*` metrics; requires a `github:pull` backend in `projects.json`) |

## Feeding the pipeline

Open Pulse keeps the GrimoireLab project configuration in sync with the
SPARQL store, so what gets indexed by CHAOSS metrics is always the same
set of repositories that appear in the graph and the RDF store. Three
CLI commands manage this:

```bash
# Build a fresh projects.json from the SPARQL store, write it locally.
open-pulse services grimoire prepare-config

# Same, then POST it to the projects-applier so Mordred picks it up.
open-pulse services grimoire apply

# Install a cron job that watches a git repo and re-applies on change.
open-pulse services grimoire install-watcher
```

The applier exposes three HTTP endpoints if you need to drive it from
outside the CLI:

- `GET /healthz` — liveness probe.
- `GET /current` — the projects.json currently in effect.
- `POST /apply` — submit a new projects.json (authenticated).

## CHAOSS document shape

Each commit lands in `git_demo_enriched` as a ~181-field document.
Authoritative names worth knowing when writing queries or notebooks:

- **Identity (post-SortingHat unification):** `Author_uuid`,
  `Author_user_name`, `Author_org_name`, `Author_multi_org_names`,
  `Author_bot`, `Author_gender`. Same family of fields exists with the
  `Commit_` prefix.
- **Raw author/committer:** lowercase `author_name`, `author_domain`,
  `committer_name`, etc.
- **Dates:** `author_date`, `author_date_hour`, `author_date_weekday`,
  `commit_date`.
- **Repo:** `origin` (git URL), `project` (Open Pulse project slug),
  `repo_name`.
- **Change shape:** `files`, `lines_added`, `lines_removed`,
  `lines_changed`, file-level child docs.

## Accessing metrics

### The Hub's CHAOSS dashboard and API

The [Hub](../hub/index.md) computes **35 CHAOSS metrics** per
repository and per GrimoireLab project, drawing on all three stores
(Neo4j, SPARQL, OpenSearch) and grouped into three buckets —
Community, Popularity and Quality. Every metric ships the exact
upstream queries it ran, so results are fully auditable. The same
computations are exposed as a JSON REST API — see the
[CHAOSS Metrics API reference](../reference/chaoss-api.md).

### Project-metric caching & weekly warm

Per-**project** metrics are expensive: the hub computes every metric for every
repo in the project live over Neo4j + SPARQL + OpenSearch (up to 150 repos ×
~35 metrics). To keep clicking a project in the hub instant, results are cached:

- **In-process TTL cache**, default **8 days** (`CHAOSS_PROJECT_CACHE_TTL_S`,
  seconds; `0` disables). A hit returns instantly and reports `cached_at`.
- **Disk-persisted** under `CHAOSS_PROJECT_CACHE_DIR`
  (default `/data/hub/chaoss-cache`, host-mounted) so the cache **survives a hub
  restart/redeploy** — it's reloaded on startup. Only the full dashboard set is
  persisted; single-metric drill-downs stay in-memory.
- **Weekly warm job** — `scripts/chaoss_warm.sh` lists every project and
  recomputes its full metric set with `?refresh=true`, populating the cache
  off-peak. Install it from cron (e.g. `0 3 * * 0`, Sundays 03:00); it uses the
  read-only `HUB_AUTH_READER` password. The 8-day TTL spans the week so a click
  always lands on a warm cache.

Force a fresh recompute any time with `?refresh=true` on the metrics endpoint
(the UI's refresh control does this). Trade-off: with a weekly warm, data can be
up to a week stale between runs — re-run the warm (or hit `refresh`) after a
large data load to update sooner.

### OpenSearch Dashboards

The browse UI lives at
[http://localhost:5601](http://localhost:5601) (direct) or
[http://localhost:7508](http://localhost:7508) (via the nginx
reverse-proxy). Both surfaces present the same dashboards. Auth uses the
`OPENSEARCH_INITIAL_ADMIN_PASSWORD` set in `infra/.env`.

### Python

OpenSearch speaks the Elasticsearch API; `opensearch-py` is the most
straightforward client:

```python
from opensearchpy import OpenSearch
import os

os_client = OpenSearch(
    hosts=[{"host": "localhost", "port": 9200, "scheme": "https"}],
    http_auth=("admin", os.environ["OPENSEARCH_INITIAL_ADMIN_PASSWORD"]),
    verify_certs=False,
)

# Commits per month across all tracked repos
body = {
    "size": 0,
    "aggs": {
        "monthly": {
            "date_histogram": {
                "field": "author_date",
                "calendar_interval": "month",
            }
        }
    },
}
result = os_client.search(index="git_demo_enriched", body=body)
for bucket in result["aggregations"]["monthly"]["buckets"]:
    print(bucket["key_as_string"], bucket["doc_count"])
```

For dataframes:

```python
import pandas as pd

buckets = result["aggregations"]["monthly"]["buckets"]
df = pd.DataFrame(buckets)
df["date"] = pd.to_datetime(df["key_as_string"])
df.set_index("date")["doc_count"].plot(title="Commits per month")
```

### Worked queries

**Top 10 most active repositories by commit count (last 12 months).**

```json
GET /git_demo_enriched/_search
{
  "size": 0,
  "query": {
    "range": { "author_date": { "gte": "now-12M/M" } }
  },
  "aggs": {
    "by_repo": {
      "terms": { "field": "repo_name", "size": 10 }
    }
  }
}
```

**Bus factor proxy: number of authors who together produced 80% of commits.**

The CHAOSS "bus factor" metric typically reduces to a percentile cut.
You can approximate it directly in OpenSearch by sorting authors by
commit count and walking the cumulative distribution; the
`Author_multi_org_names` field gives you the org-resolved identity.

**License compliance.** Pair OpenSearch counts with the SPARQL store's
`schema:license` field — see
[Metadata & Ontology](metadata-and-ontology.md) for the join.

## Cross-layer questions

Some CHAOSS-style questions naturally cross layers:

| Question                                                   | Layers                       |
| ---------------------------------------------------------- | ---------------------------- |
| "Which permissive-licensed repos have the highest contributor diversity?" | SPARQL (license) + OpenSearch (authors) |
| "Show contributor flow between two collaborating orgs over time"          | Neo4j (org links) + OpenSearch (dates)  |
| "Rank repos by activity normalised by repo age"            | OpenSearch + SPARQL (`schema:dateCreated`) |

Pipeline steps use the
[service container](../pipeline/index.md) to call the relevant clients;
notebooks can compose queries directly as shown above.
