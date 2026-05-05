"""Built-in SPARQL queries + facet definitions for the hub Projects page.

The library is hard-coded so it ships with the package and stays in sync
with the metadata extractor's JSON-LD vocabulary. User-saved queries from
the Databases page persist separately in SQLite (``data/hub/app.db``) and
sit alongside these.

Two surfaces:

* :data:`BUILTIN_QUERIES` — named, parameterised SPARQL templates that the
  UI exposes as a "Templates" dropdown on the Projects page. The
  ``params`` list drives the parameter inputs; the UI substitutes
  ``{NAME}`` placeholders before running the query.
* :data:`FACETS` — declarative description of facet dimensions (org,
  license, discipline, language, keyword, repo type). Each entry knows
  the SPARQL needed to enumerate its values + counts and the predicate
  path used when composing a filter from selected values. Both
  ``/api/projects/facets`` and ``/api/projects/build-from-filters``
  consume this list.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PREFIXES = """\
PREFIX schema: <http://schema.org/>
PREFIX rdf:    <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
"""


# ── Saved templates (the "examples") ────────────────────────────────────────

@dataclass(frozen=True)
class QueryParam:
    name: str
    type: str = "string"      # "string" | "list" | "regex"
    default: str = ""
    help: str = ""


@dataclass(frozen=True)
class BuiltinQuery:
    id: str
    name: str
    description: str
    template: str
    params: tuple[QueryParam, ...] = field(default_factory=tuple)


BUILTIN_QUERIES: tuple[BuiltinQuery, ...] = (
    BuiltinQuery(
        id="all-repos",
        name="All repositories",
        description="Every schema:SoftwareSourceCode in the store, sorted by URL.",
        template=PREFIXES + """\
SELECT ?repo WHERE {
  ?repo a schema:SoftwareSourceCode .
}
ORDER BY ?repo""",
    ),
    BuiltinQuery(
        id="by-organization",
        name="By organization",
        description="Repos whose author or publisher organization name matches exactly.",
        params=(
            QueryParam(name="ORG", default="sdsc-ordes",
                       help="Organization name as it appears in schema:name."),
        ),
        template=PREFIXES + """\
SELECT DISTINCT ?repo WHERE {
  ?repo a schema:SoftwareSourceCode ;
        (schema:author|schema:publisher)/schema:name "{ORG}" .
}
ORDER BY ?repo""",
    ),
    BuiltinQuery(
        id="by-license",
        name="By license (substring)",
        description="Repos whose schema:license URL/string contains a substring (case-insensitive).",
        params=(
            QueryParam(name="LICENSE", default="apache-2",
                       help="Substring matched against the license URL or label, lower-cased."),
        ),
        template=PREFIXES + """\
SELECT ?repo ?license WHERE {
  ?repo a schema:SoftwareSourceCode ;
        schema:license ?license .
  FILTER(CONTAINS(LCASE(STR(?license)), "{LICENSE}"))
}
ORDER BY ?repo""",
    ),
    BuiltinQuery(
        id="by-discipline",
        name="By discipline / application category",
        description="Repos tagged with a schema:applicationCategory equal to the value.",
        params=(
            QueryParam(name="CATEGORY", default="life-sciences",
                       help="Category value as it appears in the data."),
        ),
        template=PREFIXES + """\
SELECT DISTINCT ?repo WHERE {
  ?repo a schema:SoftwareSourceCode ;
        schema:applicationCategory ?cat .
  FILTER(STR(?cat) = "{CATEGORY}")
}
ORDER BY ?repo""",
    ),
    BuiltinQuery(
        id="by-programming-language",
        name="By programming language",
        description="Repos that list a particular programmingLanguage.",
        params=(
            QueryParam(name="LANGUAGE", default="Rust",
                       help="Exact language name (case-sensitive); matches GitHub-style labels."),
        ),
        template=PREFIXES + """\
SELECT DISTINCT ?repo WHERE {
  ?repo a schema:SoftwareSourceCode ;
        schema:programmingLanguage ?lang .
  FILTER(STR(?lang) = "{LANGUAGE}")
}
ORDER BY ?repo""",
    ),
    BuiltinQuery(
        id="by-keyword",
        name="By keyword / topic (regex)",
        description="Repos whose keywords/topics match a regex (case-insensitive).",
        params=(
            QueryParam(name="KEYWORD", type="regex", default="ontology",
                       help="Regex applied to schema:keywords values."),
        ),
        template=PREFIXES + """\
SELECT DISTINCT ?repo WHERE {
  ?repo a schema:SoftwareSourceCode ;
        schema:keywords ?kw .
  FILTER(REGEX(STR(?kw), "{KEYWORD}", "i"))
}
ORDER BY ?repo""",
    ),
    BuiltinQuery(
        id="org-and-license",
        name="By organization × license",
        description="Combined slice: an organization restricted to a license substring.",
        params=(
            QueryParam(name="ORG", default="sdsc-ordes"),
            QueryParam(name="LICENSE", default="mit"),
        ),
        template=PREFIXES + """\
SELECT DISTINCT ?repo WHERE {
  ?repo a schema:SoftwareSourceCode ;
        (schema:author|schema:publisher)/schema:name "{ORG}" ;
        schema:license ?license .
  FILTER(CONTAINS(LCASE(STR(?license)), "{LICENSE}"))
}
ORDER BY ?repo""",
    ),
    BuiltinQuery(
        id="recently-modified",
        name="Recently modified",
        description="Repos with schema:dateModified after a given ISO date.",
        params=(
            QueryParam(name="SINCE", default="2025-01-01",
                       help="ISO date; only repos modified on or after this date are returned."),
        ),
        template=PREFIXES + """\
SELECT ?repo ?modified WHERE {
  ?repo a schema:SoftwareSourceCode ;
        schema:dateModified ?modified .
  FILTER(STR(?modified) >= "{SINCE}")
}
ORDER BY DESC(?modified)""",
    ),
)


def builtin_query_dicts() -> list[dict[str, object]]:
    """Serialise BUILTIN_QUERIES for JSON return."""
    out: list[dict[str, object]] = []
    for q in BUILTIN_QUERIES:
        out.append({
            "id": q.id,
            "name": q.name,
            "description": q.description,
            "template": q.template,
            "params": [
                {"name": p.name, "type": p.type, "default": p.default, "help": p.help}
                for p in q.params
            ],
        })
    return out


# ── Facets (the "list of possible filters with counts") ─────────────────────

@dataclass(frozen=True)
class Facet:
    key: str                       # internal id (also the filter key)
    label: str                     # UI label
    description: str
    values_query: str              # SELECT ?value (COUNT(DISTINCT ?repo) AS ?count) ...
    predicate_path: str            # SPARQL property path between ?repo and the facet value


FACETS: tuple[Facet, ...] = (
    Facet(
        key="organization",
        label="Organization",
        description="Authors and publishers as schema:Organization (or anything with schema:name).",
        predicate_path="(schema:author|schema:publisher)/schema:name",
        values_query=PREFIXES + """\
SELECT ?value (COUNT(DISTINCT ?repo) AS ?count) WHERE {
  ?repo a schema:SoftwareSourceCode ;
        (schema:author|schema:publisher)/schema:name ?value .
}
GROUP BY ?value
ORDER BY DESC(?count) ?value""",
    ),
    Facet(
        key="license",
        label="License",
        description="schema:license values (URLs or string labels).",
        predicate_path="schema:license",
        values_query=PREFIXES + """\
SELECT ?value (COUNT(DISTINCT ?repo) AS ?count) WHERE {
  ?repo a schema:SoftwareSourceCode ;
        schema:license ?value .
}
GROUP BY ?value
ORDER BY DESC(?count) ?value""",
    ),
    Facet(
        key="discipline",
        label="Discipline / Category",
        description="schema:applicationCategory — discipline-level tags.",
        predicate_path="schema:applicationCategory",
        values_query=PREFIXES + """\
SELECT ?value (COUNT(DISTINCT ?repo) AS ?count) WHERE {
  ?repo a schema:SoftwareSourceCode ;
        schema:applicationCategory ?value .
}
GROUP BY ?value
ORDER BY DESC(?count) ?value""",
    ),
    Facet(
        key="language",
        label="Programming language",
        description="schema:programmingLanguage values.",
        predicate_path="schema:programmingLanguage",
        values_query=PREFIXES + """\
SELECT ?value (COUNT(DISTINCT ?repo) AS ?count) WHERE {
  ?repo a schema:SoftwareSourceCode ;
        schema:programmingLanguage ?value .
}
GROUP BY ?value
ORDER BY DESC(?count) ?value""",
    ),
    Facet(
        key="keyword",
        label="Keyword / Topic",
        description="schema:keywords / topic tags.",
        predicate_path="schema:keywords",
        values_query=PREFIXES + """\
SELECT ?value (COUNT(DISTINCT ?repo) AS ?count) WHERE {
  ?repo a schema:SoftwareSourceCode ;
        schema:keywords ?value .
}
GROUP BY ?value
ORDER BY DESC(?count) ?value
LIMIT 200""",
    ),
    Facet(
        key="repo_type",
        label="Repository type",
        description="rdf:type — the schema.org class(es) asserted on the resource.",
        predicate_path="a",
        values_query=PREFIXES + """\
SELECT ?value (COUNT(DISTINCT ?repo) AS ?count) WHERE {
  ?repo a ?value .
  FILTER(STRSTARTS(STR(?value), "http://schema.org/"))
}
GROUP BY ?value
ORDER BY DESC(?count) ?value""",
    ),
)


def facet_by_key(key: str) -> Facet | None:
    for f in FACETS:
        if f.key == key:
            return f
    return None


# ── Filter-builder: compose a SPARQL query from selected facet values ─────

def _escape_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _values_term(value: str) -> str:
    """Format one SPARQL VALUES term.

    URL-shaped values are bound as IRIs (``<…>``); everything else as
    string literals. This matches how the metadata extractor stores
    license / repo URLs (as URIs, per JSON-LD ``@id``) versus plain
    strings (organization names, language labels, …). Without this
    distinction, RDF term equality fails — a literal "https://…" never
    matches an IRI <https://…> even though the strings are identical.
    """
    if value.startswith(("http://", "https://")):
        return f"<{value}>"
    return f'"{_escape_literal(value)}"'


def build_filtered_query(selections: dict[str, list[str]]) -> str:
    """Build a SPARQL SELECT for repos matching every (facet → values) pair.

    Each facet contributes a triple pattern + a VALUES clause restricting
    the bound variable to the selected items. Across facets it's an AND
    (intersection); within a facet it's an OR (the VALUES list).

    Empty selection → returns "all repos".
    """
    blocks: list[str] = []
    for key, values in selections.items():
        if not values:
            continue
        facet = facet_by_key(key)
        if facet is None:
            continue
        var = f"?_{key.replace('-', '_')}_v"

        # Repo-type values are always IRIs; for everything else the
        # _values_term helper picks IRI vs literal per value based on URL
        # shape (handles license URIs, plain string literals, etc.).
        if facet.key == "repo_type":
            value_terms = " ".join(f"<{v}>" for v in values)
        else:
            value_terms = " ".join(_values_term(v) for v in values)

        triple = f"?repo {facet.predicate_path} {var} ."
        values_clause = f"VALUES {var} {{ {value_terms} }}"
        blocks.append(f"  {triple}\n  {values_clause}")

    if not blocks:
        return PREFIXES + (
            "SELECT DISTINCT ?repo WHERE {\n"
            "  ?repo a schema:SoftwareSourceCode .\n"
            "}\nORDER BY ?repo"
        )

    body = "\n".join(blocks)
    return (
        PREFIXES
        + "SELECT DISTINCT ?repo WHERE {\n"
        + "  ?repo a schema:SoftwareSourceCode .\n"
        + body
        + "\n}\nORDER BY ?repo"
    )
