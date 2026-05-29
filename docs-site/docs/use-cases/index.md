---
title: Use Cases
slug: /use-cases
---

# Use Cases

Concrete examples of what you can answer with an Open Pulse deployment.
Each scenario picks the right backing layer for the question and gives
runnable queries.

The three layers are
[Neo4j](../concepts/graph-and-semantic-data.md) for graph traversal,
[the SPARQL store](../concepts/metadata-and-ontology.md) for semantic /
metadata queries, and
[GrimoireLab on OpenSearch](../concepts/metrics-and-chaoss.md) for
time-series activity metrics. Many real questions touch two of them.

## 1. Community health: who keeps which repo alive?

**Goal.** Identify the contributors who carry a project, and how
exposure to a single person's activity has evolved.

**Layer.** GrimoireLab (commit-level history).

```python
# Top contributors over the last 6 months for one repo
from opensearchpy import OpenSearch
import os

os_client = OpenSearch(
    hosts=[{"host": "localhost", "port": 9200, "scheme": "https"}],
    http_auth=("admin", os.environ["OPENSEARCH_INITIAL_ADMIN_PASSWORD"]),
    verify_certs=False,
)

body = {
    "size": 0,
    "query": {
        "bool": {
            "filter": [
                {"term":  {"repo_name": "https://github.com/sdsc-ordes/gimie"}},
                {"range": {"author_date": {"gte": "now-6M/M"}}},
            ]
        }
    },
    "aggs": {"authors": {"terms": {"field": "Author_user_name", "size": 10}}},
}
print(os_client.search(index="git_demo_enriched", body=body)["aggregations"])
```

Pair the resulting list with the
`git-onion_demo_enriched_*` index — GrimoireLab's "onion model"
classifies authors into **core**, **regular** and **casual** tiers based
on commit share. A shrinking core tier is the bus-factor signal CHAOSS
calls out.

## 2. Cross-institutional collaboration patterns

**Goal.** Find users who contribute across multiple organisations, and
the org pairs they connect.

**Layer.** Neo4j (graph traversal).

```cypher
// Users who contribute to repos owned by more than one organisation
MATCH (u:User)-[:CONTRIBUTES_TO]->(r:Repo)<-[:OWNS]-(o:Org)
WITH u, collect(DISTINCT o.login) AS orgs
WHERE size(orgs) >= 2
RETURN u.login AS user, orgs
ORDER BY size(orgs) DESC, user
LIMIT 20
```

```cypher
// Org-to-org collaboration weight (number of shared contributors)
MATCH (o1:Org)-[:OWNS]->(:Repo)<-[:CONTRIBUTES_TO]-(u:User)
      -[:CONTRIBUTES_TO]->(:Repo)<-[:OWNS]-(o2:Org)
WHERE o1.login < o2.login
RETURN o1.login AS a, o2.login AS b, count(DISTINCT u) AS shared
ORDER BY shared DESC
LIMIT 20
```

The second query gives an undirected weighted graph that visualises
nicely in the Neo4j Browser
([http://localhost:7503](http://localhost:7503)) — drag a node, swap to
graph mode, the inter-org edges appear.

## 3. License and language landscape

**Goal.** Summarise the licenses and programming languages used by
repositories owned by a given organisation.

**Layer.** SPARQL store.

```sparql
PREFIX op:     <https://open-pulse.epfl.ch/ontology#>
PREFIX schema: <http://schema.org/>

SELECT ?license (COUNT(?repo) AS ?repos) WHERE {
  ?org op:githubOrganizationHandle "sdsc-ordes" ;
       op:owns ?repo .
  OPTIONAL { ?repo schema:license ?license }
}
GROUP BY ?license
ORDER BY DESC(?repos)
```

```sparql
PREFIX op:     <https://open-pulse.epfl.ch/ontology#>
PREFIX schema: <http://schema.org/>

SELECT ?language (COUNT(?repo) AS ?repos) WHERE {
  ?org op:githubOrganizationHandle "sdsc-ordes" ;
       op:owns ?repo .
  OPTIONAL { ?repo schema:programmingLanguage ?language }
}
GROUP BY ?language
ORDER BY DESC(?repos)
```

Swap the handle (`sdsc-ordes`, `EPFL-Open-Science`,
`SwissDataScienceCenter`, …) to compare orgs.

## 4. Walkthrough: from "who owns what" to "how active is it"

A typical evaluation question chains all three layers:

> *"For a given organisation, list its repositories, enrich them with
> license and language, then add commit activity over the last year."*

**Step 1 — graph layer.** Find the repositories.

```cypher
MATCH (o:Org {login: "EPFL-Open-Science"})-[:OWNS]->(r:Repo)
RETURN r.full_name AS handle
ORDER BY handle
```

**Step 2 — SPARQL store.** Enrich each handle with semantic metadata.

```sparql
PREFIX op:     <https://open-pulse.epfl.ch/ontology#>
PREFIX schema: <http://schema.org/>

SELECT ?handle ?license ?language ?stars WHERE {
  VALUES ?handle { "EPFL-Open-Science/EPFL_OS_Analysis" }  # ← from step 1
  ?repo op:githubRepositoryHandle ?handle ;
        op:githubRepoStars ?stars .
  OPTIONAL { ?repo schema:license ?license }
  OPTIONAL { ?repo schema:programmingLanguage ?language }
}
```

**Step 3 — GrimoireLab.** For each repo from step 1, ask OpenSearch
how many commits land per month over the last year.

```python
body = {
    "size": 0,
    "query": {
        "bool": {
            "filter": [
                {"terms": {"repo_name": ["https://github.com/EPFL-Open-Science/EPFL_OS_Analysis"]}},
                {"range": {"author_date": {"gte": "now-12M/M"}}},
            ]
        }
    },
    "aggs": {
        "monthly": {"date_histogram": {"field": "author_date", "calendar_interval": "month"}}
    },
}
```

The chain above is the same pattern the
[Quest pipeline](../architecture/index.md) automates inside the cluster
— the only difference is that here you compose it from a notebook.

## What this dataset is not

A few things the legacy documentation suggested that simply are not in
the data today, so do not write queries around them:

- **No FAIR scores.** The store does not carry FAIR maturity indicators.
- **No `isEPFL` or similar institutional flags.** Membership is
  inferred via the graph's `:Org` nodes and `:OWNS` / `:MEMBER_OF`
  edges.
- **Only one discipline value.** The `op:discipline` field exists but
  currently classifies every repository as Wikidata's
  `Q428691` ("open-source software"); finer-grained discipline mapping
  is future work.
- **No metrics REST API.** Read OpenSearch directly (`opensearchpy`) or
  via OpenSearch Dashboards; there is no public `api.openpulse.epfl.ch`
  endpoint.

## Where to go next

- **Want to add your own organisation?** See
  [Register a node](../operations/register-a-node.md) for the hosted
  side, and [Architecture](../architecture/index.md) for the pipeline
  shape.
- **Want richer dashboards?** Edit them in OpenSearch Dashboards at
  [http://localhost:5601](http://localhost:5601) and save them to the
  same OpenSearch backend the production deployment uses.
- **Want to contribute analyses?** Open a PR with a notebook
  (Jupyter or Marimo); the community will help shape it into a use case
  here.
