"""Welcome-mat example queries for the Databases console.

Each entry is shown as a clickable chip under the editor; clicking swaps
the editor's content with ``body`` (which intentionally carries inline
``--``/``#``/``//`` comments documenting the query — read-as-you-explore
beats a separate help panel).

Adding a new example: append a dict to the right list. Keep ``name``
short enough to fit a chip (~24 chars). Use the engine's native comment
syntax inside ``body``.
"""

from __future__ import annotations

from typing import Any

# ── DuckDB ───────────────────────────────────────────────────────────────
DUCKDB: list[dict[str, str]] = [
    {
        "id": "duckdb-hello",
        "name": "Hello",
        "summary": "Smallest possible query — verifies the console is wired up.",
        "body": "-- Smallest possible query: returns one row, one column.\n"
        "SELECT 42 AS answer, 'hello' AS greeting;",
    },
    {
        "id": "duckdb-list-functions",
        "name": "List functions",
        "summary": "Browse DuckDB's built-in scalar / aggregate / table functions.",
        "body": "-- DuckDB ships hundreds of built-in functions. Filter by name to find\n"
        "-- e.g. JSON helpers (`*json*`), regex (`*regex*`), or list/string ops.\n"
        "SELECT function_name, function_type, description\n"
        "FROM duckdb_functions()\n"
        "WHERE function_name ILIKE '%json%'\n"
        "ORDER BY function_name\n"
        "LIMIT 50;",
    },
    {
        "id": "duckdb-attach-app-db",
        "name": "Attach hub app DB",
        "summary": "Read the hub's SQLite saved-queries / history table directly.",
        "body": "-- DuckDB can attach SQLite files in-place. The hub's app DB lives at\n"
        "-- /data/hub/app.db and is mounted read-only inside the hub container.\n"
        "ATTACH '/data/hub/app.db' AS hub (TYPE SQLITE);\n"
        "SELECT engine, COUNT(*) AS n\n"
        "FROM hub.query_history\n"
        "GROUP BY engine\n"
        "ORDER BY n DESC;",
    },
    {
        "id": "duckdb-read-csv",
        "name": "Read CSV",
        "summary": "Auto-infer schema from any CSV under /data/.",
        "body": "-- read_csv_auto sniffs the schema. Replace the path with anything under\n"
        "-- /data/ — the hub mounts the shared data dir read-only there.\n"
        "-- Example: SELECT * FROM read_csv_auto('/data/some-export.csv') LIMIT 100;\n"
        "SELECT * FROM read_csv_auto('/data/hub/example.csv') LIMIT 25;",
    },
    {
        "id": "duckdb-list-files",
        "name": "List /data files",
        "summary": "Walk the data dir to find loadable files (CSV/Parquet/JSON).",
        "body": "-- glob() lets you discover what's actually in /data/ without `ls`.\n"
        "SELECT file, size, last_modified\n"
        "FROM glob('/data/**/*')\n"
        "ORDER BY last_modified DESC\n"
        "LIMIT 50;",
    },
]


# ── SPARQL ───────────────────────────────────────────────────────────────
SPARQL: list[dict[str, str]] = [
    {
        "id": "sparql-count-types",
        "name": "Count by type",
        "summary": "How many entities of each schema.org class are in the store.",
        "body": "# COUNT entities grouped by their rdf:type — quickest health check\n"
        "# for the SPARQL store. Uses the *raw* type IRI so you can spot any\n"
        "# unexpected classes the extractor emitted.\n"
        "PREFIX rdf:    <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n"
        "SELECT ?type (COUNT(?s) AS ?n)\n"
        "WHERE { ?s rdf:type ?type }\n"
        "GROUP BY ?type ORDER BY DESC(?n)",
    },
    {
        "id": "sparql-all-repos",
        "name": "All repos",
        "summary": "Every schema:SoftwareSourceCode resource, alphabetically.",
        "body": "# Plain list of every code repo currently in the store.\n"
        "PREFIX schema: <http://schema.org/>\n"
        "SELECT ?repo WHERE { ?repo a schema:SoftwareSourceCode }\n"
        "ORDER BY ?repo",
    },
    {
        "id": "sparql-by-license",
        "name": "Group by license",
        "summary": "Repo counts per declared license — apply this slice to projects.json.",
        "body": "# Aggregate repos by their license URI/string. Useful to drive a\n"
        "# license-scoped projects.json (Apache-2 cohort, MIT cohort, …).\n"
        "PREFIX schema: <http://schema.org/>\n"
        "SELECT ?license (COUNT(DISTINCT ?repo) AS ?repos) WHERE {\n"
        "  ?repo a schema:SoftwareSourceCode ;\n"
        "        schema:license ?license .\n"
        "}\n"
        "GROUP BY ?license ORDER BY DESC(?repos)",
    },
    {
        "id": "sparql-by-language",
        "name": "Group by language",
        "summary": "schema:programmingLanguage histogram across the repo set.",
        "body": "# Top languages across all crawled repos.\n"
        "PREFIX schema: <http://schema.org/>\n"
        "SELECT ?lang (COUNT(DISTINCT ?repo) AS ?repos) WHERE {\n"
        "  ?repo a schema:SoftwareSourceCode ;\n"
        "        schema:programmingLanguage ?lang .\n"
        "}\n"
        "GROUP BY ?lang ORDER BY DESC(?repos)",
    },
    {
        "id": "sparql-recent",
        "name": "Recently modified",
        "summary": "Repos with schema:dateModified after a given ISO date.",
        "body": "# Edit the SINCE date below to slice by recency.\n"
        "PREFIX schema: <http://schema.org/>\n"
        "SELECT ?repo ?modified WHERE {\n"
        "  ?repo a schema:SoftwareSourceCode ;\n"
        "        schema:dateModified ?modified .\n"
        '  FILTER(STR(?modified) >= "2025-01-01")\n'
        "}\n"
        "ORDER BY DESC(?modified)",
    },
    {
        "id": "sparql-keywords-top",
        "name": "Top keywords",
        "summary": "Most-tagged schema:keywords across the repo set.",
        "body": "# What topics dominate the crawled software corpus.\n"
        "PREFIX schema: <http://schema.org/>\n"
        "SELECT ?keyword (COUNT(DISTINCT ?repo) AS ?n) WHERE {\n"
        "  ?repo a schema:SoftwareSourceCode ;\n"
        "        schema:keywords ?keyword .\n"
        "}\n"
        "GROUP BY ?keyword\n"
        "ORDER BY DESC(?n) ?keyword\n"
        "LIMIT 50",
    },
    {
        "id": "sparql-distinct-orgs",
        "name": "Distinct authors / publishers",
        "summary": "Organisations attached to crawled repos via schema:author / schema:publisher.",
        "body": "# Crawls the schema:author/schema:publisher object's schema:name —\n"
        "# typically the GitHub owner or institutional handle.\n"
        "PREFIX schema: <http://schema.org/>\n"
        "SELECT ?org (COUNT(DISTINCT ?repo) AS ?n) WHERE {\n"
        "  ?repo a schema:SoftwareSourceCode ;\n"
        "        (schema:author|schema:publisher)/schema:name ?org .\n"
        "}\n"
        "GROUP BY ?org\n"
        "ORDER BY DESC(?n) ?org\n"
        "LIMIT 25",
    },
    {
        "id": "sparql-with-doi",
        "name": "Repos with a DOI",
        "summary": "Software with a registered DOI via schema:identifier or sameAs.",
        "body": "# A repo is 'citable' when it has a DOI — pull those out.\n"
        "PREFIX schema: <http://schema.org/>\n"
        "SELECT ?repo ?doi WHERE {\n"
        "  ?repo a schema:SoftwareSourceCode .\n"
        "  { ?repo schema:identifier ?doi } UNION { ?repo schema:sameAs ?doi }\n"
        '  FILTER(REGEX(STR(?doi), "doi.org|10\\\\.\\\\d{4,}", "i"))\n'
        "}\n"
        "ORDER BY ?repo\n"
        "LIMIT 50",
    },
    {
        "id": "sparql-org-license",
        "name": "Licenses inside one org",
        "summary": "License distribution for repos under a given organisation.",
        "body": "# Replace ORG_NAME with the value found in 'Distinct authors'.\n"
        "PREFIX schema: <http://schema.org/>\n"
        "SELECT ?license (COUNT(DISTINCT ?repo) AS ?n) WHERE {\n"
        "  ?repo a schema:SoftwareSourceCode ;\n"
        '        (schema:author|schema:publisher)/schema:name "sdsc-ordes" ;\n'
        "        schema:license ?license .\n"
        "}\n"
        "GROUP BY ?license\n"
        "ORDER BY DESC(?n)",
    },
    {
        "id": "sparql-discipline-language",
        "name": "Language × discipline",
        "summary": "Cross-tab schema:programmingLanguage and schema:applicationCategory.",
        "body": "# Useful to spot e.g. how much Rust appears in life sciences.\n"
        "PREFIX schema: <http://schema.org/>\n"
        "SELECT ?discipline ?lang (COUNT(DISTINCT ?repo) AS ?n) WHERE {\n"
        "  ?repo a schema:SoftwareSourceCode ;\n"
        "        schema:applicationCategory ?discipline ;\n"
        "        schema:programmingLanguage ?lang .\n"
        "}\n"
        "GROUP BY ?discipline ?lang\n"
        "ORDER BY DESC(?n)\n"
        "LIMIT 100",
    },
    {
        "id": "sparql-repos-no-license",
        "name": "Repos missing a license",
        "summary": "Quality check — software in the store with no schema:license triple.",
        "body": "# These should be flagged for follow-up.\n"
        "PREFIX schema: <http://schema.org/>\n"
        "SELECT ?repo WHERE {\n"
        "  ?repo a schema:SoftwareSourceCode .\n"
        "  FILTER NOT EXISTS { ?repo schema:license ?license }\n"
        "}\n"
        "ORDER BY ?repo\n"
        "LIMIT 50",
    },
    {
        "id": "sparql-by-org-name",
        "name": "All repos for one org",
        "summary": "Hand-pick a single organisation and list its repos.",
        "body": "# Substitute ORG_NAME with the org's schema:name.\n"
        "PREFIX schema: <http://schema.org/>\n"
        "SELECT DISTINCT ?repo WHERE {\n"
        "  ?repo a schema:SoftwareSourceCode ;\n"
        '        (schema:author|schema:publisher)/schema:name "sdsc-ordes" .\n'
        "}\n"
        "ORDER BY ?repo",
    },
]


# ── Cypher (Neo4j) ───────────────────────────────────────────────────────
# The crawler schema: ``Repo`` (full_name, owner, name), ``User``
# (login, name), ``Org`` (login, name); relationships
# ``CONTRIBUTES_TO``, ``OWNS``, ``FORK_OF``, ``MEMBER_OF``.
CYPHER: list[dict[str, str]] = [
    {
        "id": "cypher-labels",
        "name": "Node labels",
        "summary": "Count nodes per label — easiest sanity check on the graph.",
        "body": "// Node-label histogram. If this is empty, the crawler hasn't pushed\n"
        "// anything yet; bring up the crawler profile and run a quest.\n"
        "MATCH (n)\n"
        "RETURN labels(n)[0] AS label, count(*) AS n\n"
        "ORDER BY n DESC",
    },
    {
        "id": "cypher-rel-types",
        "name": "Relationship types",
        "summary": "Edge-type histogram (CONTRIBUTES_TO / OWNS / FORK_OF / MEMBER_OF).",
        "body": "// What kinds of edges the crawler actually emits.\n"
        "MATCH ()-[r]->()\n"
        "RETURN type(r) AS rel, count(*) AS n\n"
        "ORDER BY n DESC",
    },
    {
        "id": "cypher-schema-sample",
        "name": "Sample one of each label",
        "summary": "Peek at a single node per label to learn property names.",
        "body": "// Returns ONE example node per label so you can see what properties\n"
        "// exist before writing a real query.\n"
        "MATCH (n)\n"
        "WITH labels(n)[0] AS lbl, n\n"
        "ORDER BY lbl\n"
        "WITH lbl, collect(n)[0] AS sample\n"
        "RETURN lbl, sample LIMIT 30",
    },
    {
        "id": "cypher-top-orgs",
        "name": "Top orgs by repos owned",
        "summary": "Most prolific GitHub organizations in the crawl graph.",
        "body": "// Hub home leaderboard mirrors this query.\n"
        "MATCH (o:Org)-[:OWNS]->(r:Repo)\n"
        "RETURN o.login AS org, o.name AS name, count(r) AS repos\n"
        "ORDER BY repos DESC, org\n"
        "LIMIT 20",
    },
    {
        "id": "cypher-top-contributors",
        "name": "Top contributors",
        "summary": "Researchers who touch the most distinct repos.",
        "body": "// Distinct repos per user — duplicates collapsed by DISTINCT.\n"
        "MATCH (u:User)-[:CONTRIBUTES_TO]->(r:Repo)\n"
        "RETURN u.login AS login, u.name AS name,\n"
        "       count(DISTINCT r) AS repos\n"
        "ORDER BY repos DESC, login\n"
        "LIMIT 25",
    },
    {
        "id": "cypher-repos-by-community",
        "name": "Repos with most contributors",
        "summary": "Community signal — repos ranked by distinct CONTRIBUTES_TO edges.",
        "body": "// Surfaces collaborative codebases. Pair with stars/forks for a\n"
        "// proper popularity ranking.\n"
        "MATCH (r:Repo)<-[:CONTRIBUTES_TO]-(u:User)\n"
        "WITH r, count(DISTINCT u) AS n_contributors\n"
        "WHERE n_contributors > 1\n"
        "RETURN r.full_name AS repo, n_contributors\n"
        "ORDER BY n_contributors DESC\n"
        "LIMIT 25",
    },
    {
        "id": "cypher-repo-neighbours",
        "name": "1-hop neighbours of a repo",
        "summary": "Every contributor, owner, and fork of a single repository.",
        "body": "// Swap the slug below to point at a different repo. The query\n"
        "// returns one row per edge so you can see the kind explicitly.\n"
        "MATCH (n:Repo {full_name: 'sdsc-ordes/gimie'})\n"
        "OPTIONAL MATCH (n)-[r]-(m)\n"
        "RETURN type(r) AS rel,\n"
        "       startNode(r) = n AS outgoing,\n"
        "       labels(m)[0] AS kind,\n"
        "       coalesce(m.full_name, m.login) AS neighbour\n"
        "LIMIT 50",
    },
    {
        "id": "cypher-user-profile",
        "name": "Profile a user",
        "summary": "All repos a user owns AND contributes to, plus their org memberships.",
        "body": "// Replace 'cmdoret' below with the login you want to inspect.\n"
        "MATCH (u:User {login: 'cmdoret'})\n"
        "OPTIONAL MATCH (u)-[:OWNS]->(o:Repo)\n"
        "OPTIONAL MATCH (u)-[:CONTRIBUTES_TO]->(c:Repo)\n"
        "OPTIONAL MATCH (u)-[:MEMBER_OF]->(g:Org)\n"
        "RETURN u.login AS login, u.name AS name,\n"
        "       collect(DISTINCT o.full_name)[0..10] AS owns,\n"
        "       collect(DISTINCT c.full_name)[0..10] AS contributes_to,\n"
        "       collect(DISTINCT g.login)              AS orgs",
    },
    {
        "id": "cypher-shared-contributors",
        "name": "Who works across two orgs",
        "summary": "Users contributing to repos in both EPFL-themed orgs at once.",
        "body": "// Set ``orgA`` and ``orgB`` to the two communities you want to\n"
        "// intersect; returns users active in repos from both.\n"
        "WITH 'sdsc-ordes' AS orgA, 'SwissDataScienceCenter' AS orgB\n"
        "MATCH (a:Org {login: orgA})-[:OWNS]->(ra:Repo)<-[:CONTRIBUTES_TO]-(u:User)\n"
        "MATCH (b:Org {login: orgB})-[:OWNS]->(rb:Repo)<-[:CONTRIBUTES_TO]-(u)\n"
        "RETURN u.login AS login, u.name AS name,\n"
        "       count(DISTINCT ra) AS in_a,\n"
        "       count(DISTINCT rb) AS in_b\n"
        "ORDER BY in_a + in_b DESC\n"
        "LIMIT 25",
    },
    {
        "id": "cypher-forks",
        "name": "Fork lineage of a repo",
        "summary": "Walk both directions of FORK_OF from a chosen repo.",
        "body": "// Inbound = repos that forked the seed; outbound = the seed's own\n"
        "// upstream. Replace the seed below.\n"
        "MATCH (seed:Repo {full_name: 'sdsc-ordes/gimie'})\n"
        "OPTIONAL MATCH (down:Repo)-[:FORK_OF]->(seed)\n"
        "OPTIONAL MATCH (seed)-[:FORK_OF]->(up:Repo)\n"
        "RETURN seed.full_name AS seed,\n"
        "       collect(DISTINCT down.full_name) AS downstream_forks,\n"
        "       collect(DISTINCT up.full_name)   AS upstream",
    },
    {
        "id": "cypher-orphan-repos",
        "name": "Orphan repos (no contributors)",
        "summary": "Repos in the graph with zero CONTRIBUTES_TO edges — likely stale.",
        "body": "// Useful for cleanup: repos crawled but with no contributor edges.\n"
        "MATCH (r:Repo)\n"
        "WHERE NOT (r)<-[:CONTRIBUTES_TO]-()\n"
        "RETURN r.full_name AS repo,\n"
        "       coalesce(r.owner, '') AS owner\n"
        "ORDER BY r.full_name\n"
        "LIMIT 50",
    },
    {
        "id": "cypher-org-members",
        "name": "Members of an org",
        "summary": "Users with a MEMBER_OF edge into the given Org.",
        "body": "// Swap the login below. Returns the listed members; might be\n"
        "// smaller than the active-contributor set since not every user\n"
        "// announces their org membership publicly.\n"
        "MATCH (u:User)-[:MEMBER_OF]->(o:Org {login: 'sdsc-ordes'})\n"
        "RETURN u.login AS login, u.name AS name\n"
        "ORDER BY login\n"
        "LIMIT 50",
    },
    {
        "id": "cypher-multi-org-users",
        "name": "Users with multiple orgs",
        "summary": "Find researchers active across more than one institutional GitHub org.",
        "body": "// Cross-institution collaborators.\n"
        "MATCH (u:User)-[:MEMBER_OF]->(o:Org)\n"
        "WITH u, collect(DISTINCT o.login) AS orgs\n"
        "WHERE size(orgs) > 1\n"
        "RETURN u.login AS login, u.name AS name, orgs\n"
        "ORDER BY size(orgs) DESC, login\n"
        "LIMIT 50",
    },
]


# ── OpenSearch ───────────────────────────────────────────────────────────
# Two query languages share the OpenSearch tab: the SQL plugin (one-liner
# style) and the raw Search DSL (full power, JSON). The console picks
# which endpoint to hit based on the chip's `mode` field.
OPENSEARCH: list[dict[str, Any]] = [
    {
        "id": "os-sql-indices",
        "name": "List indices",
        "mode": "sql",
        "summary": "SQL-flavoured `SHOW TABLES` — every index visible to the user.",
        "body": "-- The OpenSearch SQL plugin treats indices as tables.\n"
        "-- SHOW lists everything visible to the current user.\n"
        "SHOW TABLES LIKE '%';",
    },
    {
        "id": "os-sql-git-count",
        "name": "Count git commits",
        "mode": "sql",
        "summary": "How many git commit docs landed in the GrimoireLab raw index.",
        "body": "-- Mordred writes raw git commits to git_demo_raw. Each doc = one commit.\n"
        "SELECT COUNT(*) AS commits FROM git_demo_raw;",
    },
    {
        "id": "os-sql-by-repo",
        "name": "Commits per repo",
        "mode": "sql",
        "summary": "Top repos in the index by commit volume.",
        "body": "-- The crawler's `origin` field stores the repo URL on every row.\n"
        "SELECT origin AS repo, COUNT(*) AS commits\n"
        "FROM git_demo_raw\n"
        "GROUP BY origin\n"
        "ORDER BY commits DESC\n"
        "LIMIT 25;",
    },
    {
        "id": "os-dsl-recent-commits",
        "name": "Recent commits (DSL)",
        "mode": "dsl",
        "summary": "Most recent N commits across every repo, raw _search DSL.",
        "body": "// Search-DSL form. Edit `index` to target a different one.\n"
        "// `_source` cherry-picks only the fields we display in the table.\n"
        "{\n"
        '  "index": "git_demo_raw",\n'
        '  "size": 25,\n'
        '  "_source": ["data.repository", "data.commit", "data.message", "data.AuthorDate"],\n'
        '  "sort": [{ "data.AuthorDate": { "order": "desc" } }]\n'
        "}",
    },
    {
        "id": "os-dsl-search-message",
        "name": "Search commit messages",
        "mode": "dsl",
        "summary": "Full-text search in the commit message field.",
        "body": "// Returns commits whose message matches a phrase. Edit `query` to adjust.\n"
        "{\n"
        '  "index": "git_demo_raw",\n'
        '  "size": 25,\n'
        '  "_source": ["data.repository", "data.commit", "data.message"],\n'
        '  "query": {\n'
        '    "match": { "data.message": "merge pull request" }\n'
        "  }\n"
        "}",
    },
    {
        "id": "os-sql-top-repos-enriched",
        "name": "Top repos · enriched",
        "mode": "sql",
        "summary": "Commit volume per repo in the enriched git index.",
        "body": "-- The enriched index uses ``origin`` for the repo URL — same as\n"
        "-- the activity panel on the hub.\n"
        "SELECT origin AS repo, COUNT(*) AS commits\n"
        "FROM git_demo_enriched\n"
        "GROUP BY origin\n"
        "ORDER BY commits DESC\n"
        "LIMIT 25;",
    },
    {
        "id": "os-dsl-activity-month",
        "name": "Activity timeline (one repo)",
        "mode": "dsl",
        "summary": "Monthly commits histogram for a single repo — drives the entity-page sparkline.",
        "body": "// Edit ``origin`` to point at any repo. ``grimoire_creation_date``\n"
        "// is the typed-date field used by GrimoireLab; ``date`` on raw\n"
        "// docs isn't always indexed as a date.\n"
        "{\n"
        '  "index": "git_*_enriched",\n'
        '  "size": 0,\n'
        '  "query": {\n'
        '    "term": { "origin": "https://github.com/EPFL-ENAC/enackubespray" }\n'
        "  },\n"
        '  "aggs": {\n'
        '    "by_month": {\n'
        '      "date_histogram": {\n'
        '        "field": "grimoire_creation_date",\n'
        '        "calendar_interval": "month",\n'
        '        "min_doc_count": 1\n'
        "      }\n"
        "    }\n"
        "  }\n"
        "}",
    },
    {
        "id": "os-dsl-top-contributors",
        "name": "Top contributors network-wide",
        "mode": "dsl",
        "summary": "Aggregate cardinality of distinct authors across every enriched git index.",
        "body": "// Top author_name values by commit count. The terms agg uses the\n"
        "// keyword sub-field so the analyzer doesn't tokenise names.\n"
        "{\n"
        '  "index": "git_*_enriched",\n'
        '  "size": 0,\n'
        '  "aggs": {\n'
        '    "by_author": {\n'
        '      "terms": { "field": "author_name.keyword", "size": 20 }\n'
        "    }\n"
        "  }\n"
        "}",
    },
    {
        "id": "os-dsl-most-active",
        "name": "Most active repos this year",
        "mode": "dsl",
        "summary": "Top origins by commit count restricted to a recent window.",
        "body": "// Adjust the ``gte`` value to shorten / extend the window.\n"
        "{\n"
        '  "index": "git_*_enriched",\n'
        '  "size": 0,\n'
        '  "query": {\n'
        '    "range": { "grimoire_creation_date": { "gte": "2024-01-01" } }\n'
        "  },\n"
        '  "aggs": {\n'
        '    "by_repo": {\n'
        '      "terms": { "field": "origin", "size": 20 }\n'
        "    }\n"
        "  }\n"
        "}",
    },
    {
        "id": "os-dsl-author-on-repo",
        "name": "Commits by author × repo",
        "mode": "dsl",
        "summary": "Top authors on a specific repo (a small co-occurrence query).",
        "body": "// Combine origin filter + author terms to see who drives a repo.\n"
        "{\n"
        '  "index": "git_*_enriched",\n'
        '  "size": 0,\n'
        '  "query": {\n'
        '    "term": { "origin": "https://github.com/EPFL-ENAC/enackubespray" }\n'
        "  },\n"
        '  "aggs": {\n'
        '    "by_author": {\n'
        '      "terms": { "field": "author_name.keyword", "size": 15 }\n'
        "    }\n"
        "  }\n"
        "}",
    },
    {
        "id": "os-sql-issues",
        "name": "GitHub issues count",
        "mode": "sql",
        "summary": "How many GitHub issue / PR docs landed in the github index.",
        "body": "-- Mordred writes one document per issue/PR action. Filter by\n"
        "-- pull_request_p to separate PRs from issues.\n"
        "SELECT COUNT(*) AS issues FROM github_demo_raw;",
    },
    {
        "id": "os-dsl-recent-issues",
        "name": "Recent issues (DSL)",
        "mode": "dsl",
        "summary": "Newest N issues / PRs across the github index.",
        "body": "// _source plucks just the fields useful for a status table.\n"
        "{\n"
        '  "index": "github_*_enriched",\n'
        '  "size": 25,\n'
        '  "_source": ["origin", "title", "state", "user_login", "created_at"],\n'
        '  "sort": [{ "created_at": { "order": "desc" } }]\n'
        "}",
    },
    {
        "id": "os-dsl-list-indices",
        "name": "List indices (DSL)",
        "mode": "dsl",
        "summary": "Raw _cat/indices via _search — handy when SQL plugin is disabled.",
        "body": "// _cat/indices doesn't return JSON; this DSL trick gives the same\n"
        "// data through _search, one bucket per index.\n"
        "{\n"
        '  "index": "_all",\n'
        '  "size": 0,\n'
        '  "aggs": {\n'
        '    "by_index": {\n'
        '      "terms": { "field": "_index", "size": 50 }\n'
        "    }\n"
        "  }\n"
        "}",
    },
]


def by_engine() -> dict[str, list[dict[str, Any]]]:
    """Return chips per engine.

    DuckDB is intentionally excluded from the browser-facing list — the
    DuckDB backend route still exists (for advanced users hitting the API
    directly), but the Databases console focuses on the three stores users
    actually run queries against day-to-day: SPARQL, Cypher, OpenSearch.
    """
    return {
        "sparql": SPARQL,
        "cypher": CYPHER,
        "opensearch": OPENSEARCH,
    }
