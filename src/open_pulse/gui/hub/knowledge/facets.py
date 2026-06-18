"""Cached facet values for the catalog Filters modal — and the SPARQL-backed
resolution that turns a facet selection into a set of repositories.

The hub catalog grid is backed by DuckDB; type / source / language filter it
directly on their own columns. The remaining "graph" properties — licence,
owning org, discipline, repository type and cited works — live only in the GME
SPARQL store, keyed by the repository IRI (``https://github.com/owner/repo``).
This module:

* precomputes the *top values* of each property (cheap per-predicate
  ``GROUP BY``) so the Filters modal can show "the main ones" without hammering
  the store on every open, and
* resolves a multi-facet selection back to a page of matching repositories
  (:func:`graph_repo_page`) — the SPARQL store does the filtering, sorting and
  pagination; the catalog then hydrates only the page's rows from DuckDB.

Discipline objects are bare Wikidata Q-IDs with no label in the graph, so the
top ones are resolved to human names via the Wikidata API (only the ~12 shown,
memoised). Everything else carries a readable label already.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request
from typing import Any

from . import duckdb_browser as ddb
from . import qdrant, stores

log = logging.getLogger(__name__)

_TOP_N = 12

# (key, label, predicate, kind) — ``kind`` drives how the object is labelled.
#   literal     — object is a plain literal (licence name); value == label
#   language    — same, but filters the grid on its own DuckDB column
#   iri_local   — ontology IRI; label is the prettified local name
#   iri_owner   — a github.com/<org> IRI; label is the org login
#   iri_qid     — Wikidata entity IRI; label resolved via the Wikidata API
#   iri_doi     — a DOI/URL IRI; label is the human path (e.g. 10.5334/x)
_SPARQL_FACETS: list[tuple[str, str, str, str]] = [
    ("language", "Language", "https://openpulse.science/git-metadata-extractor#primary_language", "language"),
    ("license", "License", "https://openpulse.science/git-metadata-extractor#license_name", "literal"),
    ("repository_type", "Repository type", "https://open-pulse.epfl.ch/ontology#repositoryType", "iri_local"),
    ("owner", "Owner / org", "https://open-pulse.epfl.ch/ontology#ownedBy", "iri_owner"),
    ("discipline", "Discipline", "https://open-pulse.epfl.ch/ontology#discipline", "iri_qid"),
    ("citation", "Cited works", "http://schema.org/citation", "iri_doi"),
]

# Which facets filter via a DuckDB column ("column") vs. via the SPARQL repo
# index ("graph"). Everything except language resolves through the graph.
_COLUMN_FACETS = {"language"}

# key → (predicate, object_is_iri) for the graph-filter SPARQL. Language is
# included so it can be AND-ed into a graph query when both are active.
_GRAPH_PREDS: dict[str, tuple[str, bool]] = {
    key: (pred, kind not in ("literal", "language"))
    for key, _label, pred, kind in _SPARQL_FACETS
}

# Repo IRIs live under this host; the catalog ref / repo_id is the tail.
_REPO_PREFIX = "https://github.com/"
_STARS_PRED = "https://open-pulse.epfl.ch/ontology#githubRepoStars"
_NAME_PRED = "http://schema.org/name"
_PUSHED_PRED = "https://openpulse.science/git-metadata-extractor#pushed_at"


def _strip_scheme(iri: str) -> str:
    s = re.sub(r"^https?://", "", (iri or "").strip(), flags=re.I)
    return re.sub(r"^www\.", "", s, flags=re.I).rstrip("/")


def _pretty_local(iri: str) -> str:
    """``…#EducationalResource`` → ``Educational Resource``."""
    local = re.split(r"[#/]", iri.rstrip("/"))[-1]
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", local) or local


def _get_json(url: str, *, accept: str = "application/json", timeout: int = 6) -> Any:
    """GET ``url`` and parse JSON. Raises on any failure (callers catch)."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "open-pulse-hub/1.0 (mailto:ops@epfl.ch)", "Accept": accept}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        return json.load(r)


# ── Wikidata label resolution (disciplines) ────────────────────────────────
_WD_LABELS: dict[str, str] = {}


def _qid(iri: str) -> str:
    m = re.search(r"(Q\d+)$", iri.rstrip("/"))
    return m.group(1) if m else ""


def _resolve_qids(qids: list[str]) -> dict[str, str]:
    """Wikidata Q-ID → English label, batched + memoised. Best-effort: any
    unresolved id just falls back to the bare Q-ID at the call site."""
    todo = [q for q in dict.fromkeys(qids) if q and q not in _WD_LABELS]
    for i in range(0, len(todo), 50):  # API caps at 50 ids/call
        batch = todo[i : i + 50]
        url = (
            "https://www.wikidata.org/w/api.php?action=wbgetentities&ids="
            + "|".join(batch)
            + "&props=labels&languages=en&format=json"
        )
        try:
            data = _get_json(url)
            for q, ent in (data.get("entities") or {}).items():
                lbl = ((ent.get("labels") or {}).get("en") or {}).get("value")
                if lbl:
                    _WD_LABELS[q] = lbl
        except Exception as exc:  # noqa: BLE001
            log.info("wikidata label fetch failed (%s): %s", batch[:3], exc)
            break
    return _WD_LABELS


# ── owner + citation human-name resolution ──────────────────────────────────
# These facets show bare ids (DOIs, ORCIDs, github handles). We resolve a
# human label — work title / person / org name — preferring the local indices,
# then falling back to a public API for misses. Memoised so the Refresh button
# never refetches an id we've already seen this process.
_CROSSREF_TITLES: dict[str, str] = {}
_ORCID_NAMES: dict[str, str] = {}
# Bound the per-build API fallbacks so a cold facet build can't hang on a slow
# endpoint × 12 ids. Local hits are unbounded (they're cheap).
_MAX_API_CALLS = 14


def _store_map(collection: str, key_col: str, val_col: str, keys: list[str]) -> dict[str, str]:
    """``{lower(key): val}`` for the rows of ``collection`` whose ``key_col``
    matches one of ``keys`` (case-insensitive). Empty when unavailable."""
    keys = [k for k in dict.fromkeys(keys) if k]
    b = ddb._BACKING.get(collection)
    if not b or not keys:
        return {}
    src = ddb._source_expr(b)
    try:
        with ddb._connect(b.db_path) as con:
            placeholders = ", ".join("?" for _ in keys)
            rows = con.execute(
                f'SELECT lower(CAST("{key_col}" AS VARCHAR)), CAST("{val_col}" AS VARCHAR) '
                f'FROM {src} WHERE lower(CAST("{key_col}" AS VARCHAR)) IN ({placeholders})',
                [k.lower() for k in keys],
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        log.info("store_map %s.%s failed: %s", collection, key_col, exc)
        return {}
    return {row[0]: row[1] for row in rows if row[1]}


def _clean_title(title: str) -> str:
    """Strip the inline HTML some titles carry (Crossref/OpenAlex) + clamp."""
    t = re.sub(r"<[^>]+>", "", title or "").strip()
    return t[:90]


def _doi_title(doi: str) -> str:
    """DOI → work title, memoised (empty memoised too). Crossref covers most
    journal/conference DOIs; DataCite covers the Zenodo / EPFL-thesis DOIs
    (10.5281, 10.5075, …) that are common in this corpus."""
    if doi in _CROSSREF_TITLES:
        return _CROSSREF_TITLES[doi]
    title = ""
    try:
        data = _get_json(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}")
        arr = (data.get("message") or {}).get("title") or []
        title = arr[0] if arr else ""
    except Exception as exc:  # noqa: BLE001
        log.info("crossref title failed (%s): %s", doi, exc)
    if not title:
        try:
            data = _get_json(f"https://api.datacite.org/dois/{urllib.parse.quote(doi)}")
            arr = ((data.get("data") or {}).get("attributes") or {}).get("titles") or []
            title = (arr[0].get("title") if arr else "") or ""
        except Exception as exc:  # noqa: BLE001
            log.info("datacite title failed (%s): %s", doi, exc)
    _CROSSREF_TITLES[doi] = title
    return title


def _orcid_name(orcid: str) -> str:
    """ORCID id → person name via the ORCID public API, memoised."""
    if orcid in _ORCID_NAMES:
        return _ORCID_NAMES[orcid]
    name = ""
    try:
        data = _get_json(f"https://pub.orcid.org/v3.0/{orcid}/personal-details")
        n = data.get("name") or {}
        given = (n.get("given-names") or {}).get("value") or ""
        family = (n.get("family-name") or {}).get("value") or ""
        name = (f"{given} {family}").strip() or (n.get("credit-name") or {}).get("value") or ""
    except Exception as exc:  # noqa: BLE001
        log.info("orcid name failed (%s): %s", orcid, exc)
    _ORCID_NAMES[orcid] = name
    return name


def _enrich_citation(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cited works: resolve each DOI to its title (bold ``label``) and keep the
    DOI as the secondary ``sub``. Local ``works`` first, then Crossref."""
    titles = _store_map("works", "doi", "title", [v["value"] for v in values])
    api_budget = _MAX_API_CALLS
    for v in values:
        doi = v["label"]  # iri_doi label is already the DOI path
        title = titles.get(v["value"].lower(), "")
        if not title and api_budget > 0:
            title = _doi_title(doi)
            api_budget -= 1
        if title:
            v["label"], v["sub"] = _clean_title(title), doi
        else:
            v["sub"] = ""
    return values


def _enrich_owner(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Owner / org: resolve the person name (ORCID) or user/org name (github
    handle) as the bold ``label``, keeping the handle/ORCID as ``sub``."""
    orcid_iris = [v["value"] for v in values if "orcid.org/" in v["value"]]
    gh_logins = [
        _strip_scheme(v["value"]).rsplit("/", 1)[-1]
        for v in values if "github.com/" in v["value"]
    ]
    authors = _store_map("authors", "orcid", "display_name", orcid_iris)
    gh_users = _store_map("github_users", "login", "name", gh_logins)
    gh_orgs = _store_map("github_organizations", "login", "name", gh_logins)
    api_budget = _MAX_API_CALLS
    for v in values:
        iri, idl = v["value"], v["label"]
        name = ""
        if "orcid.org/" in iri:
            name = authors.get(iri.lower(), "")
            if not name and api_budget > 0:
                name = _orcid_name(idl)
                api_budget -= 1
        elif "github.com/" in iri:
            login = idl.lower()
            name = gh_users.get(login) or gh_orgs.get(login) or ""
        if name and name.strip().lower() != idl.lower():
            v["label"], v["sub"] = name.strip()[:60], idl
        else:
            v["sub"] = ""
    return values


_ENRICHERS = {"citation": _enrich_citation, "owner": _enrich_owner}


# ── facet value computation ─────────────────────────────────────────────────
def _sparql_facet(predicate: str, kind: str) -> list[dict[str, Any]]:
    rows = stores.sparql_select(
        f"SELECT ?o (COUNT(*) AS ?n) WHERE {{ ?s <{predicate}> ?o }} "
        f"GROUP BY ?o ORDER BY DESC(?n) LIMIT {_TOP_N}"
    )
    out: list[dict[str, Any]] = []
    qids: list[str] = []
    for r in rows:
        o = r.get("o", {}).get("value", "")
        if not o:
            continue
        try:
            count = int(r.get("n", {}).get("value") or 0)
        except (TypeError, ValueError):
            count = 0
        if kind in ("literal", "language"):
            label = o
        elif kind == "iri_local":
            label = _pretty_local(o)
        elif kind == "iri_owner":
            label = _strip_scheme(o).rsplit("/", 1)[-1]  # org login
        elif kind == "iri_doi":
            label = re.sub(r"^doi\.org/", "", _strip_scheme(o))
        elif kind == "iri_qid":
            label = _qid(o) or _strip_scheme(o).rsplit("/", 1)[-1]
            qids.append(_qid(o))
        else:
            label = _strip_scheme(o).rsplit("/", 1)[-1]
        # ``sub`` is the secondary (normal-weight) line — set by the
        # owner/citation enrichers when a human label is resolved.
        out.append({"value": o, "label": label, "count": count, "sub": ""})

    # Disciplines: swap the bare Q-IDs for human labels where resolvable.
    if kind == "iri_qid" and qids:
        labels = _resolve_qids(qids)
        for v in out:
            v["label"] = labels.get(v["label"], v["label"])
    return out


def gather(refresh: bool = False) -> list[dict[str, Any]]:
    """All filterable facets with their top values —
    ``[{key,label,kind,mode,values:[{value,label,count}]}]``. Empty facets are
    dropped. Cached; the data moves only on GME re-ingest. ``refresh=True``
    recomputes and refreshes the cache (the Filters modal's Refresh button)."""

    def _build() -> list[dict[str, Any]]:
        facets: list[dict[str, Any]] = []
        for key, label, pred, kind in _SPARQL_FACETS:
            try:
                values = _sparql_facet(pred, kind)
                enrich = _ENRICHERS.get(key)
                if enrich and values:
                    values = enrich(values)
            except Exception as exc:  # noqa: BLE001
                log.info("facet %s failed: %s", key, exc)
                values = []
            if values:
                facets.append({
                    "key": key,
                    "label": label,
                    "kind": kind,
                    "mode": "column" if key in _COLUMN_FACETS else "graph",
                    "values": values,
                })
        return facets

    # Cache key bumped to v3 — values gained a resolved human ``label`` +
    # ``sub`` (id) for the owner / citation facets.
    return qdrant.cached_panel("facets", "v3", _build, force=refresh)


# ── graph-facet resolution → a page of repositories ─────────────────────────
def _sparql_literal(v: str) -> str:
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _in_clause(values: list[str], is_iri: bool) -> str:
    if is_iri:
        return ", ".join(f"<{v}>" for v in values)
    return ", ".join(_sparql_literal(v) for v in values)


def _graph_where(selections: dict[str, list[str]]) -> str:
    """The shared WHERE body (one triple + FILTER IN per active facet)."""
    parts: list[str] = []
    for i, (key, values) in enumerate(selections.items()):
        pred_kind = _GRAPH_PREDS.get(key)
        if not pred_kind or not values:
            continue
        pred, is_iri = pred_kind
        parts.append(
            f"?s <{pred}> ?o{i} . FILTER(?o{i} IN ({_in_clause(values, is_iri)}))"
        )
    return " ".join(parts)


# UI sort key → (sort predicate, descending). Default is stars.
_GRAPH_SORT = {
    "stars": (_STARS_PRED, True),
    "recent": (_PUSHED_PRED, True),
    "name": (_NAME_PRED, False),
}


def graph_repo_page(
    selections: dict[str, list[str]], *, sort: str = "", page: int = 1, size: int = 24
) -> dict[str, Any]:
    """Resolve a multi-facet selection to one sorted, paginated page of repo
    refs. AND across facets, OR within a facet. Returns
    ``{"refs": ["owner/repo", …], "total": int}`` (refs in display order).

    Only the graph-resolvable facets in ``selections`` are honoured; unknown
    keys are ignored. Empty / unresolvable selection → empty page.
    """
    selections = {k: [v for v in vs if v] for k, vs in (selections or {}).items()}
    selections = {k: vs for k, vs in selections.items() if vs and k in _GRAPH_PREDS}
    where = _graph_where(selections)
    if not where:
        return {"refs": [], "total": 0}

    page = max(1, int(page or 1))
    size = max(1, min(60, int(size or 24)))
    offset = (page - 1) * size
    sort_pred, desc = _GRAPH_SORT.get(sort, _GRAPH_SORT["stars"])
    # Full XSD IRI for the numeric cast — the endpoint has no ``xsd:`` prefix
    # bound, so ``xsd:integer(…)`` would 400.
    order_key = (
        "<http://www.w3.org/2001/XMLSchema#integer>(?k)"
        if sort_pred == _STARS_PRED else "?k"
    )
    direction = f"DESC({order_key})" if desc else f"ASC({order_key})"

    try:
        total_rows = stores.sparql_select(
            f"SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE {{ {where} }}"
        )
        total = int((total_rows[0].get("n", {}) if total_rows else {}).get("value") or 0)
    except Exception as exc:  # noqa: BLE001
        log.info("graph facet count failed: %s", exc)
        return {"refs": [], "total": 0}

    try:
        rows = stores.sparql_select(
            f"SELECT ?s (MAX(?kv) AS ?k) WHERE {{ {where} "
            f"OPTIONAL {{ ?s <{sort_pred}> ?kv }} }} "
            f"GROUP BY ?s ORDER BY {direction} LIMIT {size} OFFSET {offset}"
        )
    except Exception as exc:  # noqa: BLE001
        log.info("graph facet page failed: %s", exc)
        return {"refs": [], "total": total}

    refs: list[str] = []
    for r in rows:
        s = r.get("s", {}).get("value", "")
        if s.startswith(_REPO_PREFIX):
            ref = s[len(_REPO_PREFIX):].rstrip("/")
            if ref:
                refs.append(ref)
    return {"refs": refs, "total": total}
