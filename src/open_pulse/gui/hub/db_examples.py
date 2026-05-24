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
        "summary": "Citable software via schema:citation (direct DOI URL or via ScholarlyArticle).",
        "body": "# DOIs live in two places in the GME v3 hybrid output:\n"
        "#   - directly on the repo as a literal DOI URL: ?repo schema:citation <doi>\n"
        "#   - on a linked ScholarlyArticle node: ?repo schema:citation ?article ; ?article schema:identifier <doi>\n"
        "# The pre-v3 chip used ``schema:identifier`` / ``schema:sameAs`` on the\n"
        "# repo itself — those predicates are unused in v3, so it returned 0.\n"
        "PREFIX schema: <http://schema.org/>\n"
        "SELECT DISTINCT ?repo ?doi WHERE {\n"
        "  ?repo a schema:SoftwareSourceCode .\n"
        "  {\n"
        "    # Direct: repo cites a DOI URL\n"
        "    ?repo schema:citation ?doi .\n"
        '    FILTER(REGEX(STR(?doi), "doi\\\\.org|10\\\\.\\\\d{4,}", "i"))\n'
        "  } UNION {\n"
        "    # Indirect: repo cites a ScholarlyArticle whose identifier is the DOI\n"
        "    ?repo schema:citation ?article .\n"
        "    ?article schema:identifier ?doi .\n"
        '    FILTER(REGEX(STR(?doi), "doi\\\\.org|10\\\\.\\\\d{4,}", "i"))\n'
        "  }\n"
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
    # ── pulse: ontology (project-specific facets) ─────────────────────────
    {
        "id": "sparql-org-type",
        "name": "Orgs by type",
        "summary": "pulse:OrganizationType histogram — University / ResearchInstitution / etc.",
        "body": "# Counts the ORGS (distinct) under each pulse:OrganizationType.\n"
        "# Switch to count(?repo) (no DISTINCT on org) for repos-per-type,\n"
        "# the same figure shown in the Overview's 'Organization type' card.\n"
        "PREFIX pulse:  <https://open-pulse.epfl.ch/ontology#>\n"
        "PREFIX schema: <http://schema.org/>\n"
        "SELECT ?type (COUNT(DISTINCT ?org) AS ?orgs) WHERE {\n"
        "  ?org pulse:OrganizationType ?type .\n"
        "}\n"
        "GROUP BY ?type ORDER BY DESC(?orgs)",
    },
    {
        "id": "sparql-repo-type",
        "name": "Repos by artefact type",
        "summary": "pulse:repositoryType — Software / Documentation / EducationalResource / Data.",
        "body": "# rdf:type is always SoftwareSourceCode for repos; the real artefact\n"
        "# kind lives on pulse:repositoryType. Useful when filtering a\n"
        "# Project to one cohort (e.g. teaching materials only).\n"
        "PREFIX pulse:  <https://open-pulse.epfl.ch/ontology#>\n"
        "PREFIX schema: <http://schema.org/>\n"
        "SELECT ?type (COUNT(DISTINCT ?repo) AS ?n) WHERE {\n"
        "  ?repo a schema:SoftwareSourceCode ;\n"
        "        pulse:repositoryType ?type .\n"
        "}\n"
        "GROUP BY ?type ORDER BY DESC(?n)",
    },
    {
        "id": "sparql-discipline-pulse",
        "name": "Top disciplines",
        "summary": "pulse:discipline (Wikidata Q-IDs) — research areas across repos.",
        "body": "# discipline targets are Wikidata IRIs (Q428691 = computer engineering, …)\n"
        "# The Hub UI resolves them to English labels via wbgetentities;\n"
        "# raw Q-IDs are what you see directly in the SPARQL result.\n"
        "PREFIX pulse:  <https://open-pulse.epfl.ch/ontology#>\n"
        "PREFIX schema: <http://schema.org/>\n"
        "SELECT ?discipline (COUNT(DISTINCT ?repo) AS ?n) WHERE {\n"
        "  ?repo a schema:SoftwareSourceCode ;\n"
        "        pulse:discipline ?discipline .\n"
        "}\n"
        "GROUP BY ?discipline ORDER BY DESC(?n)",
    },
    {
        "id": "sparql-org-type-x-license",
        "name": "Org type × license",
        "summary": "Cross-tab pulse:OrganizationType with schema:license — who picks what.",
        "body": "# Useful question: do Universities pick GPL more often than\n"
        "# PrivateCompanies? This pivot answers it. The pulse:ownedBy\n"
        "# path connects a repo back to its owning org's type.\n"
        "PREFIX pulse:  <https://open-pulse.epfl.ch/ontology#>\n"
        "PREFIX schema: <http://schema.org/>\n"
        "SELECT ?org_type ?license (COUNT(DISTINCT ?repo) AS ?n) WHERE {\n"
        "  ?repo a schema:SoftwareSourceCode ;\n"
        "        pulse:ownedBy/pulse:OrganizationType ?org_type ;\n"
        "        schema:license ?license .\n"
        "}\n"
        "GROUP BY ?org_type ?license\n"
        "ORDER BY DESC(?n) LIMIT 100",
    },
    {
        "id": "sparql-people-by-location",
        "name": "People by location",
        "summary": "gme-internal:location histogram — raw location strings from GitHub bios.",
        "body": "# Plain string straight from the GitHub profile, so expect free-form\n"
        "# variants ('Lausanne' / 'Lausanne, CH' / 'EPFL Lausanne'). Useful\n"
        "# as a top-of-funnel signal before any ROR/normalisation pass.\n"
        "# (For org-level country rollups via ROR look at\n"
        "# gme-internal:ror_country — but that points at a blank-node\n"
        "# structure, not a flat literal.)\n"
        "PREFIX gme-internal: <https://openpulse.science/git-metadata-extractor#>\n"
        "SELECT ?location (COUNT(DISTINCT ?s) AS ?n) WHERE {\n"
        "  ?s gme-internal:location ?location .\n"
        '  FILTER (STR(?location) != "")\n'
        "}\n"
        "GROUP BY ?location ORDER BY DESC(?n) LIMIT 50",
    },
    {
        "id": "sparql-power-users",
        "name": "Most-followed users (GH)",
        "summary": "gme-internal:followers_count — global GitHub follower count, ranked.",
        "body": "# Unlike the Neo4j FOLLOWS edge (followers *inside the crawl*),\n"
        "# this is the raw GitHub follower number — useful to spot well-\n"
        "# known names in the corpus.\n"
        "PREFIX schema:       <http://schema.org/>\n"
        "PREFIX gme-internal: <https://openpulse.science/git-metadata-extractor#>\n"
        "SELECT ?person ?followers ?location WHERE {\n"
        "  ?person a schema:Person ;\n"
        "          gme-internal:followers_count ?followers .\n"
        "  OPTIONAL { ?person gme-internal:location ?location }\n"
        "}\n"
        "ORDER BY DESC(?followers) LIMIT 25",
    },
    {
        "id": "sparql-people-by-company",
        "name": "Contributors by company",
        "summary": "gme-internal:company — declared affiliation on GitHub profile.",
        "body": "# `company` is the raw string the user typed in their GitHub\n"
        "# profile; expect typos / variants ('EPFL' vs '@EPFL' vs 'epfl.ch').\n"
        "# Normalise downstream if you need a clean institutional rollup.\n"
        "PREFIX schema:       <http://schema.org/>\n"
        "PREFIX gme-internal: <https://openpulse.science/git-metadata-extractor#>\n"
        "SELECT ?company (COUNT(DISTINCT ?person) AS ?n) WHERE {\n"
        "  ?person a schema:Person ;\n"
        "          gme-internal:company ?company .\n"
        "}\n"
        "GROUP BY ?company ORDER BY DESC(?n) LIMIT 50",
    },
    {
        "id": "sparql-archived-repos",
        "name": "Archived repos",
        "summary": "gme-internal:archived = true — cold storage in the corpus.",
        "body": "# Quick way to spot inactive code. ``archived`` is a typed boolean\n"
        "# (xsd:boolean), so the literal must carry the datatype; plain\n"
        '# string "true" won\'t match.\n'
        "# Useful for filtering a projects.json to active-only repos\n"
        "# (negate the filter) or for picking historic snapshots to study.\n"
        "PREFIX xsd:          <http://www.w3.org/2001/XMLSchema#>\n"
        "PREFIX gme-internal: <https://openpulse.science/git-metadata-extractor#>\n"
        "SELECT ?repo WHERE {\n"
        '  ?repo gme-internal:archived "true"^^xsd:boolean .\n'
        "}\n"
        "ORDER BY ?repo LIMIT 100",
    },
    {
        "id": "sparql-keywords-internal",
        "name": "Keywords (GME)",
        "summary": "gme-internal:keywords histogram — GME-derived tags vs schema:keywords.",
        "body": "# gme-internal:keywords is the GME's own extraction pass — broader\n"
        "# coverage than schema:keywords (which comes from the repo's\n"
        "# declared topics). Comparing the two surfaces inferred tags the\n"
        "# repo owner didn't add themselves.\n"
        "PREFIX gme-internal: <https://openpulse.science/git-metadata-extractor#>\n"
        "SELECT ?keyword (COUNT(DISTINCT ?subj) AS ?n) WHERE {\n"
        "  ?subj gme-internal:keywords ?keyword .\n"
        "}\n"
        "GROUP BY ?keyword ORDER BY DESC(?n) LIMIT 50",
    },
    {
        "id": "sparql-people-with-blog",
        "name": "People with a blog",
        "summary": "Persons whose GitHub profile lists a blog/website URL.",
        "body": "# Useful for outreach: a non-empty blog URL is a strong signal that\n"
        "# the person maintains a public web presence. The Hub entity page\n"
        "# already links these directly.\n"
        "PREFIX schema:       <http://schema.org/>\n"
        "PREFIX gme-internal: <https://openpulse.science/git-metadata-extractor#>\n"
        "SELECT ?person ?blog ?company WHERE {\n"
        "  ?person a schema:Person ;\n"
        "          gme-internal:blog ?blog .\n"
        '  FILTER (STR(?blog) != "")\n'
        "  OPTIONAL { ?person gme-internal:company ?company }\n"
        "}\n"
        "ORDER BY ?person LIMIT 50",
    },
    {
        "id": "sparql-repo-1hop",
        "name": "Repo 1-hop neighborhood",
        "summary": "All direct neighbors of a repo (out + in) with their properties.",
        "body": "# Every triple where the target repo is subject OR object, plus the\n"
        "# properties of each neighbor. Swap the IRI to pivot the view.\n"
        "PREFIX schema: <http://schema.org/>\n"
        "PREFIX pulse:  <https://open-pulse.epfl.ch/ontology#>\n"
        "PREFIX gi:     <https://openpulse.science/git-metadata-extractor#>\n"
        "PREFIX org:    <http://www.w3.org/ns/org#>\n"
        "PREFIX rdf:    <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n"
        "SELECT ?direction ?p ?neighbor ?neighbor_pred ?neighbor_value WHERE {\n"
        "  VALUES ?repo { <https://github.com/sdsc-ordes/gimie> }\n"
        "  {\n"
        "    ?repo ?p ?neighbor .\n"
        '    BIND("out" AS ?direction)\n'
        "    OPTIONAL { ?neighbor ?neighbor_pred ?neighbor_value }\n"
        "  } UNION {\n"
        "    ?neighbor ?p ?repo .\n"
        '    BIND("in" AS ?direction)\n'
        "    OPTIONAL { ?neighbor ?neighbor_pred ?neighbor_value }\n"
        "  }\n"
        "}\n"
        "ORDER BY ?direction ?p ?neighbor\n"
        "LIMIT 300",
    },
    {
        "id": "sparql-repo-construct",
        "name": "Repo subgraph (CONSTRUCT)",
        "summary": "Materialize the repo's 1-hop subgraph as triples — load into a viewer.",
        "body": "# CONSTRUCT a self-contained subgraph around one repo: its own\n"
        "# outgoing triples, its incoming triples, and the properties of\n"
        "# every neighbor. Result is N-Triples — paste into WebVOWL / gephi /\n"
        "# another store. Swap the IRI to re-anchor.\n"
        "PREFIX schema: <http://schema.org/>\n"
        "PREFIX pulse:  <https://open-pulse.epfl.ch/ontology#>\n"
        "PREFIX gi:     <https://openpulse.science/git-metadata-extractor#>\n"
        "CONSTRUCT {\n"
        "  ?repo ?p1 ?neighbor .\n"
        "  ?neighbor ?p2 ?o2 .\n"
        "  ?inbound ?p3 ?repo .\n"
        "  ?inbound ?p4 ?o4 .\n"
        "} WHERE {\n"
        "  VALUES ?repo { <https://github.com/sdsc-ordes/gimie> }\n"
        "  {\n"
        "    ?repo ?p1 ?neighbor .\n"
        "    OPTIONAL { ?neighbor ?p2 ?o2 }\n"
        "  } UNION {\n"
        "    ?inbound ?p3 ?repo .\n"
        "    OPTIONAL { ?inbound ?p4 ?o4 }\n"
        "  }\n"
        "}",
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
    # ── PR-8 edges: follows / stars / watches / issues / PRs ───────────────
    {
        "id": "cypher-top-followed",
        "name": "Top followed users",
        "summary": "Users with the most FOLLOWS edges pointing at them — social hubs.",
        "body": "// FOLLOWS direction in the crawler: (follower)-[:FOLLOWS]->(followed).\n"
        "// Counting incoming edges gives the followed user's audience size\n"
        "// *within the crawl* — much smaller than GitHub's global follower\n"
        "// count, but tells you who's central in this corpus.\n"
        "MATCH (u:User)<-[:FOLLOWS]-(f:User)\n"
        "RETURN u.login AS login, u.name AS name,\n"
        "       count(DISTINCT f) AS followers_in_graph\n"
        "ORDER BY followers_in_graph DESC, login\n"
        "LIMIT 25",
    },
    {
        "id": "cypher-most-starred",
        "name": "Most-starred repos",
        "summary": "Repos ranked by STARRED in-degree from crawled users.",
        "body": "// STARRED is populated when round-N expands user profiles. The\n"
        "// count below is stars *within our crawl* — a proxy for community\n"
        "// attention restricted to the corpus, complementary to raw\n"
        "// GitHub stars (which lives on the Repo node when crawl_issues\n"
        "// is on).\n"
        "MATCH (r:Repo)<-[:STARRED]-(u:User)\n"
        "RETURN r.full_name AS repo, count(DISTINCT u) AS in_graph_stars\n"
        "ORDER BY in_graph_stars DESC, repo\n"
        "LIMIT 25",
    },
    {
        "id": "cypher-stars-and-contributes",
        "name": "Stars → contributors funnel",
        "summary": "Users who STARRED a repo AND also CONTRIBUTES_TO it — engaged users.",
        "body": "// Crossing STARRED with CONTRIBUTES_TO surfaces the strongest\n"
        "// engagement signal: people who liked the repo enough to also\n"
        "// commit to it. Useful for outreach / governance pitches.\n"
        "MATCH (u:User)-[:STARRED]->(r:Repo)\n"
        "MATCH (u)-[:CONTRIBUTES_TO]->(r)\n"
        "RETURN r.full_name AS repo,\n"
        "       count(DISTINCT u) AS engaged_users\n"
        "ORDER BY engaged_users DESC, repo\n"
        "LIMIT 25",
    },
    {
        "id": "cypher-pr-openers",
        "name": "Top PR openers",
        "summary": "Users who opened the most pull requests across the crawl.",
        "body": "// OPENED_PR edges are populated when crawl_prs=true at crawl\n"
        "// time; with only contributors crawled, this count will be low.\n"
        "// Combine with REVIEWED_PR for a full code-review picture.\n"
        "MATCH (u:User)-[:OPENED_PR]->(r:Repo)\n"
        "RETURN u.login AS login, u.name AS name,\n"
        "       count(*) AS prs_opened,\n"
        "       count(DISTINCT r) AS distinct_repos\n"
        "ORDER BY prs_opened DESC, login\n"
        "LIMIT 25",
    },
    {
        "id": "cypher-code-reviewers",
        "name": "Code reviewers leaderboard",
        "summary": "Who reviews the most PRs (REVIEWED_PR) — different signal from authoring.",
        "body": "// PR reviews are gating work; the people who do most of it are\n"
        "// often invisible to commit-count leaderboards. This query lifts\n"
        "// them out.\n"
        "MATCH (u:User)-[:REVIEWED_PR]->(r:Repo)\n"
        "RETURN u.login AS login, u.name AS name,\n"
        "       count(*) AS reviews,\n"
        "       count(DISTINCT r) AS distinct_repos\n"
        "ORDER BY reviews DESC, login\n"
        "LIMIT 25",
    },
    {
        "id": "cypher-discussion-hubs",
        "name": "Issue/PR discussion hubs",
        "summary": "Repos with the most COMMENTED_ON activity — high-discussion projects.",
        "body": "// COMMENTED_ON is unified across issues + PRs in the crawler.\n"
        "// A repo with many distinct commenters but few contributors is\n"
        "// a discussion-heavy / governance-heavy project rather than a\n"
        "// commit-heavy one.\n"
        "MATCH (r:Repo)<-[:COMMENTED_ON]-(u:User)\n"
        "WITH r, count(DISTINCT u) AS commenters\n"
        "OPTIONAL MATCH (r)<-[:CONTRIBUTES_TO]-(c:User)\n"
        "WITH r, commenters, count(DISTINCT c) AS contributors\n"
        "RETURN r.full_name AS repo, commenters, contributors,\n"
        "       round(1.0 * commenters / CASE WHEN contributors=0 THEN 1 ELSE contributors END, 2) AS commenter_to_contrib_ratio\n"
        "ORDER BY commenters DESC, repo\n"
        "LIMIT 25",
    },
    {
        "id": "cypher-dependency-hot",
        "name": "Most-depended-on repos",
        "summary": "Repos with the most DEPENDS_ON edges pointing at them — corpus libraries.",
        "body": "// DEPENDS_ON is populated when crawl_dependencies=true. Inbound\n"
        "// degree = how many other crawled repos pull this one in as a\n"
        "// dependency. Surfaces the foundational libraries of the corpus.\n"
        "MATCH (lib:Repo)<-[:DEPENDS_ON]-(client:Repo)\n"
        "RETURN lib.full_name AS library,\n"
        "       count(DISTINCT client) AS reverse_deps\n"
        "ORDER BY reverse_deps DESC, library\n"
        "LIMIT 25",
    },
    {
        "id": "cypher-watch-no-contrib",
        "name": "Watchers who never contributed",
        "summary": "Users with WATCHES but no CONTRIBUTES_TO — passive followers worth pinging.",
        "body": "// A repo's lurker set: people receiving notifications but never\n"
        "// pushing code. Useful for picking outreach targets when bootstrapping\n"
        "// a new contributor cohort.\n"
        "MATCH (u:User)-[:WATCHES]->(r:Repo)\n"
        "WHERE NOT (u)-[:CONTRIBUTES_TO]->(r)\n"
        "RETURN r.full_name AS repo,\n"
        "       count(DISTINCT u) AS passive_watchers,\n"
        "       collect(DISTINCT u.login)[0..5] AS sample_logins\n"
        "ORDER BY passive_watchers DESC, repo\n"
        "LIMIT 25",
    },
    {
        "id": "cypher-repo-1hop",
        "name": "Repo 1-hop neighborhood",
        "summary": "All direct nodes connected to a repo, with edge type + direction.",
        "body": "// Center on one Repo and emit every 1-hop neighbor with full props.\n"
        "// Swap full_name to pivot the inspection — works as-is for the\n"
        "// crawler-backfilled owner field.\n"
        "WITH 'sdsc-ordes/gimie' AS target\n"
        "MATCH (r:Repo {full_name: target})\n"
        "OPTIONAL MATCH (r)-[rel]-(n)\n"
        "RETURN labels(r)[0]              AS center_label,\n"
        "       r.full_name               AS center,\n"
        "       properties(r)             AS center_props,\n"
        "       type(rel)                 AS edge_type,\n"
        "       CASE WHEN startNode(rel) = r THEN 'out' ELSE 'in' END AS direction,\n"
        "       labels(n)[0]              AS neighbor_label,\n"
        "       coalesce(n.full_name, n.login) AS neighbor_id,\n"
        "       properties(n)             AS neighbor_props\n"
        "ORDER BY edge_type, direction, neighbor_id\n"
        "LIMIT 200",
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
