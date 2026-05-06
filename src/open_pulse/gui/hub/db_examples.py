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
                "  FILTER(STR(?modified) >= \"2025-01-01\")\n"
                "}\n"
                "ORDER BY DESC(?modified)",
    },
]


# ── Cypher (Neo4j) ───────────────────────────────────────────────────────
CYPHER: list[dict[str, str]] = [
    {
        "id": "cypher-labels",
        "name": "Node labels",
        "summary": "Count nodes per label — the easiest sanity check on the graph.",
        "body": "// Node-label histogram. If this is empty, the crawler hasn't pushed\n"
                "// anything yet; bring up the crawler profile and run a quest.\n"
                "MATCH (n)\n"
                "RETURN labels(n) AS labels, count(*) AS n\n"
                "ORDER BY n DESC LIMIT 25",
    },
    {
        "id": "cypher-rel-types",
        "name": "Relationship types",
        "summary": "Edge-type histogram (DEPENDS_ON / CONTRIBUTES_TO / FORKS / …).",
        "body": "// Relationship-type histogram. Drives the schema view in the\n"
                "// Neo4j Browser; useful for exploring what edges the crawler exposes.\n"
                "MATCH ()-[r]->()\n"
                "RETURN type(r) AS rel, count(*) AS n\n"
                "ORDER BY n DESC",
    },
    {
        "id": "cypher-top-repos",
        "name": "Top repos by stars",
        "summary": "Pulls the most-starred repos in the graph.",
        "body": "// Replace the property name if your model uses a different field —\n"
                "// run the 'Schema sample' example first to confirm what's stored.\n"
                "MATCH (r:Repository)\n"
                "WHERE r.stars IS NOT NULL\n"
                "RETURN r.full_name AS repo, r.stars AS stars\n"
                "ORDER BY stars DESC LIMIT 25",
    },
    {
        "id": "cypher-deps",
        "name": "Dependents of a repo",
        "summary": "Walk the DEPENDS_ON graph one hop out from a chosen repo.",
        "body": "// Substitute the seed repo in the WHERE clause; returns its first-hop\n"
                "// dependents. Useful for visualising who relies on a given library.\n"
                "MATCH (seed:Repository)<-[:DEPENDS_ON]-(d:Repository)\n"
                "WHERE seed.full_name = 'sdsc-ordes/gimie'\n"
                "RETURN d.full_name AS dependent, d.stars AS stars\n"
                "ORDER BY stars DESC LIMIT 50",
    },
    {
        "id": "cypher-schema-sample",
        "name": "Sample one of each label",
        "summary": "Peek at a single node per label to learn the property names.",
        "body": "// Returns ONE example node per label so you can see what properties\n"
                "// exist before writing a real query.\n"
                "MATCH (n)\n"
                "WITH labels(n)[0] AS lbl, n\n"
                "ORDER BY lbl\n"
                "WITH lbl, collect(n)[0] AS sample\n"
                "RETURN lbl, sample LIMIT 30",
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
                "  \"index\": \"git_demo_raw\",\n"
                "  \"size\": 25,\n"
                "  \"_source\": [\"data.repository\", \"data.commit\", \"data.message\", \"data.AuthorDate\"],\n"
                "  \"sort\": [{ \"data.AuthorDate\": { \"order\": \"desc\" } }]\n"
                "}",
    },
    {
        "id": "os-dsl-search-message",
        "name": "Search commit messages",
        "mode": "dsl",
        "summary": "Full-text search in the commit message field.",
        "body": "// Returns commits whose message matches a phrase. Edit `query` to adjust.\n"
                "{\n"
                "  \"index\": \"git_demo_raw\",\n"
                "  \"size\": 25,\n"
                "  \"_source\": [\"data.repository\", \"data.commit\", \"data.message\"],\n"
                "  \"query\": {\n"
                "    \"match\": { \"data.message\": \"merge pull request\" }\n"
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
