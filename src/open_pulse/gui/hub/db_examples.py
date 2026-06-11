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
        "id": 'sparql-epfl-repos',
        "name": 'EPFL repositories',
        "summary": "Software owned by an EPFL-affiliated org (handle contains 'epfl').",
        "body": '# Repos linked to their owning Org via pulse:ownedBy; the org\'s GitHub\n# handle is pulse:githubOrganizationHandle. Filter to EPFL labs/orgs.\nPREFIX schema: <http://schema.org/>\nPREFIX pulse:  <https://open-pulse.epfl.ch/ontology#>\nSELECT ?org_handle ?repo WHERE {\n  ?repo a schema:SoftwareSourceCode ;\n        pulse:ownedBy ?org .\n  ?org pulse:githubOrganizationHandle ?org_handle .\n  FILTER(CONTAINS(LCASE(?org_handle), "epfl"))\n}\nORDER BY ?org_handle ?repo\nLIMIT 100',
    },
    {
        "id": 'sparql-by-discipline',
        "name": 'Top research disciplines',
        "summary": 'Repos per Wikidata discipline (pulse:discipline) — the research areas covered.',
        "body": '# Disciplines are Wikidata Q-codes (e.g. Q428691 = computer engineering).\nPREFIX schema: <http://schema.org/>\nPREFIX pulse:  <https://open-pulse.epfl.ch/ontology#>\nSELECT ?discipline (COUNT(DISTINCT ?repo) AS ?repos) WHERE {\n  ?repo a schema:SoftwareSourceCode ;\n        pulse:discipline ?discipline .\n}\nGROUP BY ?discipline ORDER BY DESC(?repos)\nLIMIT 25',
    },
    {
        "id": 'sparql-with-doi',
        "name": 'Citable software (DOIs)',
        "summary": 'Repos linked to a DOI via schema:citation — research output with a paper.',
        "body": '# DOIs appear either directly as a cited URL or via a ScholarlyArticle.\nPREFIX schema: <http://schema.org/>\nSELECT DISTINCT ?repo ?doi WHERE {\n  ?repo a schema:SoftwareSourceCode .\n  { ?repo schema:citation ?doi .\n    FILTER(REGEX(STR(?doi), "doi\\\\.org|10\\\\.\\\\d{4,}", "i")) }\n  UNION\n  { ?repo schema:citation ?article . ?article schema:identifier ?doi .\n    FILTER(REGEX(STR(?doi), "doi\\\\.org|10\\\\.\\\\d{4,}", "i")) }\n}\nORDER BY ?repo\nLIMIT 50',
    },
    {
        "id": 'sparql-lang-discipline',
        "name": 'Language × discipline',
        "summary": 'Cross-tab programming language with research discipline.',
        "body": '# e.g. how much Rust shows up in the life sciences.\nPREFIX schema: <http://schema.org/>\nPREFIX pulse:  <https://open-pulse.epfl.ch/ontology#>\nSELECT ?discipline ?lang (COUNT(DISTINCT ?repo) AS ?n) WHERE {\n  ?repo a schema:SoftwareSourceCode ;\n        pulse:discipline ?discipline ;\n        schema:programmingLanguage ?lang .\n}\nGROUP BY ?discipline ?lang ORDER BY DESC(?n)\nLIMIT 100',
    },
    {
        "id": 'sparql-by-language',
        "name": 'Group by language',
        "summary": 'schema:programmingLanguage histogram across the repo set.',
        "body": 'PREFIX schema: <http://schema.org/>\nSELECT ?lang (COUNT(DISTINCT ?repo) AS ?repos) WHERE {\n  ?repo a schema:SoftwareSourceCode ;\n        schema:programmingLanguage ?lang .\n}\nGROUP BY ?lang ORDER BY DESC(?repos)',
    },
    {
        "id": 'sparql-by-license',
        "name": 'Group by license',
        "summary": 'Repo counts per declared license.',
        "body": 'PREFIX schema: <http://schema.org/>\nSELECT ?license (COUNT(DISTINCT ?repo) AS ?repos) WHERE {\n  ?repo a schema:SoftwareSourceCode ;\n        schema:license ?license .\n}\nGROUP BY ?license ORDER BY DESC(?repos)',
    },
    {
        "id": 'sparql-recent',
        "name": 'Recently active repos',
        "summary": 'Repos pushed after a given date (gme-internal:pushed_at).',
        "body": '# Edit the SINCE date to slice by recency.\nPREFIX schema: <http://schema.org/>\nPREFIX gi:     <https://openpulse.science/git-metadata-extractor#>\nSELECT ?repo ?pushed WHERE {\n  ?repo a schema:SoftwareSourceCode ;\n        gi:pushed_at ?pushed .\n  FILTER(STR(?pushed) >= "2025-01-01")\n}\nORDER BY DESC(?pushed)\nLIMIT 50',
    },
]


# ── Cypher (Neo4j) ───────────────────────────────────────────────────────
# The crawler schema:
#   Nodes: ``Repo`` (full_name, owner, name), ``User`` (login, name),
#          ``Org`` (login, name).
#   Edges (core, from the initial crawler): ``CONTRIBUTES_TO``, ``OWNS``,
#          ``FORK_OF``, ``MEMBER_OF``, ``DEPENDS_ON``.
#   Edges (PR-8 opt-ins, populated when ``crawl_issues`` / ``crawl_prs``
#          are on at crawl time): ``FOLLOWS`` (User→User),
#          ``STARRED`` / ``WATCHES`` (User→Repo), ``OPENED_ISSUE`` /
#          ``OPENED_PR`` (User→Repo), ``COMMENTED_ON`` (User→Repo on
#          issues + PRs), ``REVIEWED_PR`` (User→Repo).
CYPHER: list[dict[str, str]] = [
    {
        "id": 'cypher-epfl-top-starred',
        "name": 'Most successful EPFL repos',
        "summary": 'EPFL-affiliated repositories ranked by in-graph stars.',
        "body": '// "Most successful" EPFL software, by STARRED in-degree from crawled\n// users (raw GitHub stars aren\'t on the node — this is a corpus proxy).\n// EPFL is matched by the owning org\'s handle containing \'epfl\'.\nMATCH (o:Org)-[:OWNS]->(r:Repo)\nWHERE toLower(o.login) CONTAINS \'epfl\'\nOPTIONAL MATCH (r)<-[:STARRED]-(u:User)\nRETURN o.name AS org, r.full_name AS repo, count(DISTINCT u) AS stars\nORDER BY stars DESC, repo\nLIMIT 25',
    },
    {
        "id": 'cypher-org-starred',
        "name": 'Top repos in one organization',
        "summary": 'Most-starred repositories owned by a given GitHub org — swap the handle.',
        "body": "// Change the handle to any organization. Its repos, ranked by\n// in-graph stars (STARRED edges from crawled users).\nMATCH (o:Org)-[:OWNS]->(r:Repo)\nWHERE o.login = 'https://github.com/epfLLM'\nOPTIONAL MATCH (r)<-[:STARRED]-(u:User)\nRETURN r.full_name AS repo, count(DISTINCT u) AS stars\nORDER BY stars DESC, repo\nLIMIT 25",
    },
    {
        "id": 'cypher-org-top-starred',
        "name": "Each org's flagship repo",
        "summary": 'For every organization, its single most-starred repository.',
        "body": '// One row per Org: its top repo by in-graph stars — a leaderboard of\n// flagship projects across all organizations in the corpus.\nMATCH (o:Org)-[:OWNS]->(r:Repo)\nOPTIONAL MATCH (r)<-[:STARRED]-(u:User)\nWITH o, r, count(DISTINCT u) AS stars\nORDER BY stars DESC\nWITH o, head(collect({repo: r.full_name, stars: stars})) AS top\nRETURN o.login AS org, o.name AS name, top.repo AS top_repo, top.stars AS stars\nORDER BY stars DESC, org\nLIMIT 25',
    },
    {
        "id": 'cypher-most-starred',
        "name": 'Most-starred repos (overall)',
        "summary": 'Repositories ranked by STARRED in-degree across the whole corpus.',
        "body": '// Community attention within the crawl, irrespective of owner.\nMATCH (r:Repo)<-[:STARRED]-(u:User)\nRETURN r.full_name AS repo, count(DISTINCT u) AS stars\nORDER BY stars DESC, repo\nLIMIT 25',
    },
    {
        "id": 'cypher-top-orgs',
        "name": 'Top orgs by repos owned',
        "summary": 'Most prolific GitHub organizations in the graph.',
        "body": 'MATCH (o:Org)-[:OWNS]->(r:Repo)\nRETURN o.login AS org, o.name AS name, count(r) AS repos\nORDER BY repos DESC, org\nLIMIT 25',
    },
    {
        "id": 'cypher-repos-by-community',
        "name": 'Repos with most contributors',
        "summary": 'Where the largest contributor communities have gathered.',
        "body": 'MATCH (u:User)-[:CONTRIBUTES_TO]->(r:Repo)\nRETURN r.full_name AS repo, count(DISTINCT u) AS contributors\nORDER BY contributors DESC, repo\nLIMIT 25',
    },
    {
        "id": 'cypher-top-contributors',
        "name": 'Top contributors',
        "summary": 'People who touch the most distinct repositories.',
        "body": "// Distinct repos per user; bots ([bot]) excluded.\nMATCH (u:User)-[:CONTRIBUTES_TO]->(r:Repo)\nWHERE NOT u.login CONTAINS '[bot]'\nRETURN u.login AS login, u.name AS name, count(DISTINCT r) AS repos\nORDER BY repos DESC, login\nLIMIT 25",
    },
    {
        "id": 'cypher-org-deps',
        "name": 'Most-used dependencies in an org',
        "summary": "What an organization's code relies on most (DEPENDS_ON). EPFL by default.",
        "body": "// Across every repo the org owns, which dependencies recur most.\n// Swap the handle filter for any organization.\nMATCH (o:Org)-[:OWNS]->(r:Repo)-[:DEPENDS_ON]->(dep:Repo)\nWHERE toLower(o.login) CONTAINS 'epfl'\nRETURN dep.full_name AS dependency, count(DISTINCT r) AS used_by_repos\nORDER BY used_by_repos DESC, dependency\nLIMIT 25",
    },
    {
        "id": 'cypher-user-deps',
        "name": "A contributor's dependencies",
        "summary": 'The dependency footprint of one person across the repos they contribute to.',
        "body": "// Swap the login. Across every repo this user contributes to, which\n// dependencies show up most often — their effective tech stack.\nMATCH (u:User {login: 'https://github.com/hawkinsp'})-[:CONTRIBUTES_TO]->(r:Repo)-[:DEPENDS_ON]->(dep:Repo)\nRETURN dep.full_name AS dependency, count(DISTINCT r) AS in_repos\nORDER BY in_repos DESC, dependency\nLIMIT 25",
    },
    {
        "id": 'cypher-dependency-hot',
        "name": 'Most-depended-on repos',
        "summary": 'Ecosystem hubs — repositories the most other repos DEPENDS_ON.',
        "body": 'MATCH (r:Repo)<-[:DEPENDS_ON]-(dependent:Repo)\nRETURN r.full_name AS dependency, count(DISTINCT dependent) AS dependents\nORDER BY dependents DESC, dependency\nLIMIT 25',
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
        '      "terms": { "field": "author_name", "size": 20 }\n'
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
        '      "terms": { "field": "author_name", "size": 15 }\n'
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
    {
        "id": "os-dsl-activity-month-all",
        "name": "Monthly commits (all repos)",
        "mode": "dsl",
        "summary": "Network-wide date_histogram by month — pick 'Time series' on the result.",
        "body": "// size:0 → no hits; the agg buckets become tabular rows (key, doc_count).\n"
        "// Switch the result's 'View as' to Time series to plot it.\n"
        "{\n"
        '  "index": "git_*_enriched",\n'
        '  "size": 0,\n'
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
        "id": "os-dsl-commits-by-repo",
        "name": "Commits per repo (terms)",
        "mode": "dsl",
        "summary": "Top repos by commit count — clean terms agg; plots as a bar/scatter.",
        "body": "// Each bucket → one row [repo, doc_count].\n"
        "{\n"
        '  "index": "git_*_enriched",\n'
        '  "size": 0,\n'
        '  "aggs": {\n'
        '    "by_repo": { "terms": { "field": "origin", "size": 20 } }\n'
        "  }\n"
        "}",
    },
    {
        "id": "os-dsl-author-metrics",
        "name": "Author metrics (4-D scatter)",
        "mode": "dsl",
        "summary": "Per-author label + 4 numeric metrics — exercises the x/y/color/size scatter.",
        "body": "// Sub-aggs become extra numeric columns:\n"
        "//   [author, doc_count, commits, lines_added, lines_removed, files_touched].\n"
        "// Switch 'View as' to 2-D Scatter and map columns to x / y / color / size.\n"
        "{\n"
        '  "index": "git_*_enriched",\n'
        '  "size": 0,\n'
        '  "aggs": {\n'
        '    "by_author": {\n'
        '      "terms": { "field": "author_name", "size": 40 },\n'
        '      "aggs": {\n'
        '        "commits": { "value_count": { "field": "author_uuid" } },\n'
        '        "lines_added": { "sum": { "field": "lines_added" } },\n'
        '        "lines_removed": { "sum": { "field": "lines_removed" } },\n'
        '        "files_touched": { "sum": { "field": "files" } }\n'
        "      }\n"
        "    }\n"
        "  }\n"
        "}",
    },
    {
        "id": "os-dsl-author-by-repo-crosstab",
        "name": "Author × repo (cross-tab)",
        "mode": "dsl",
        "summary": "Nested terms-over-terms — flattens to [repo, author, doc_count] rows.",
        "body": "// Outer terms on origin, inner terms on author — one row per pair.\n"
        "{\n"
        '  "index": "git_*_enriched",\n'
        '  "size": 0,\n'
        '  "aggs": {\n'
        '    "by_repo": {\n'
        '      "terms": { "field": "origin", "size": 10 },\n'
        '      "aggs": {\n'
        '        "by_author": { "terms": { "field": "author_name", "size": 10 } }\n'
        "      }\n"
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
