#!/usr/bin/env python3
"""Backfill the Neo4j crawler graph with GME enrichment held in Oxigraph.

Closes the Crawler ⇄ GME/Oxigraph loop: the GME resolves ORCID / Infoscience
IDs and ROR-org affiliations into the SPARQL named graphs, but the Neo4j crawler
graph only has the bare GitHub structure. This script joins on the GitHub handle
(``pulse:githubUsername`` → ``User.login``, normalising bare handles to full
URLs) and writes the enrichment back into Neo4j:

  * sets ``User.orcid`` and ``User.infoscience``
  * MERGEs ``(:User)-[:AFFILIATED_WITH]->(:RorOrg {url})``

Idempotent (SET / MERGE), safe to re-run. Intended to run after each quest so
Neo4j and Oxigraph never drift (roadmap item: make this a pipeline step).

Run inside open-pulse-cli (reaches oxigraph-open-pulse:7878 + neo4j-open-pulse:7687,
has the neo4j driver and NEO4J_AUTH in the env):

    docker exec open-pulse-cli python3 scripts/backfill_neo4j_from_oxigraph.py

First run (2026-06-07): AFFILIATED_WITH 373 → 3,405; Users w/ ORCID 826 → 1,909;
Users w/ Infoscience ~0 → 746.
"""
import os, urllib.request, urllib.parse, json
from neo4j import GraphDatabase

OXI = os.environ.get("OXIGRAPH_QUERY_URL", "http://oxigraph-open-pulse:7878/query")
BOLT = os.environ.get("NEO4J_BOLT_URL", "bolt://neo4j-open-pulse:7687")


def sparql(query: str):
    data = urllib.parse.urlencode({"query": query}).encode()
    req = urllib.request.Request(
        OXI, data=data,
        headers={"Accept": "application/sparql-results+json",
                 "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=240) as r:
        return json.loads(r.read())["results"]["bindings"]


def norm(gh: str) -> str:
    """Bare handle -> canonical GitHub URL (Oxigraph mixes both forms)."""
    return gh if gh.startswith("http") else "https://github.com/" + gh


def main() -> None:
    # 1) identity: github url -> orcid / infoscience
    ident: dict[str, dict] = {}
    for b in sparql(
        'PREFIX schema: <http://schema.org/> '
        'PREFIX pulse: <https://open-pulse.epfl.ch/ontology#> '
        'SELECT DISTINCT ?gh ?orcid ?info WHERE { GRAPH ?g { '
        '?p a schema:Person ; pulse:githubUsername ?gh . '
        'OPTIONAL{?p pulse:orcidIdentifier ?orcid} '
        'OPTIONAL{?p pulse:infosciencePersonIdentifier ?info} } }'
    ):
        gh = norm(b["gh"]["value"]); d = ident.setdefault(gh, {})
        if b.get("orcid"): d["orcid"] = b["orcid"]["value"]
        if b.get("info"): d["info"] = b["info"]["value"]
    id_rows = [{"gh": g, "orcid": v.get("orcid"), "info": v.get("info")}
               for g, v in ident.items() if v]

    # 2) person iri -> github url (resolves orcid-keyed persons in memberships)
    ghmap = {b["p"]["value"]: norm(b["gh"]["value"]) for b in sparql(
        'PREFIX pulse: <https://open-pulse.epfl.ch/ontology#> '
        'SELECT DISTINCT ?p ?gh WHERE { GRAPH ?g { ?p pulse:githubUsername ?gh } }')}

    # 3) memberships -> (github url, ror url, org name)
    aff = set()
    for b in sparql(
        'PREFIX schema: <http://schema.org/> PREFIX org: <http://www.w3.org/ns/org#> '
        'SELECT DISTINCT ?m ?ror ?name WHERE { GRAPH ?g { '
        '?m a org:Membership ; org:organization ?o . '
        '?o schema:identifier ?ror ; schema:name ?name . '
        'FILTER(CONTAINS(STR(?ror),"ror.org")) } }'
    ):
        m = b["m"]["value"]
        person = m.split("__")[0] if "__" in m else m
        if person.startswith("https://github.com/"):
            gh = person
        elif person.startswith("http"):
            gh = ghmap.get(person)
        else:
            gh = "https://github.com/" + person
        if gh:
            aff.add((gh, b["ror"]["value"], b["name"]["value"]))
    aff = [{"gh": g, "ror": r, "name": n} for g, r, n in aff]

    print(f"identity rows={len(id_rows)}  affiliation rows={len(aff)}", flush=True)
    drv = GraphDatabase.driver(BOLT, auth=tuple(os.environ["NEO4J_AUTH"].split("/", 1)))
    with drv.session() as s:
        n1 = s.run(
            "UNWIND $rows AS row MATCH (u:User {login: row.gh}) "
            "SET u.orcid=coalesce(row.orcid,u.orcid), "
            "u.infoscience=coalesce(row.info,u.infoscience) RETURN count(u) AS n",
            rows=id_rows).single()["n"]
        n2 = s.run(
            "UNWIND $aff AS a MATCH (u:User {login:a.gh}) "
            "MERGE (o:RorOrg {url:a.ror}) ON CREATE SET o.name=a.name "
            "MERGE (u)-[:AFFILIATED_WITH]->(o) RETURN count(*) AS n",
            aff=aff).single()["n"]
        print(f"users enriched={n1}  affiliation MERGEs={n2}", flush=True)
    drv.close()


if __name__ == "__main__":
    main()
