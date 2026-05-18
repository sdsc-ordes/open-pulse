"""Projects builder: query SPARQL, post to applier — both proxied through the hub."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException

from ..auth import get_settings, require_auth
from ..queries import (
    FACETS,
    build_filtered_query,
    builtin_query_dicts,
)

# Reuse the pipeline step's builder so the hub button + the quest step
# return the exact same projects.json shape — they're literally the same
# function, just with different triggers.
from open_pulse.pipeline.apply_grimoire_projects import (
    build_owner_grouped_projects,
    post_to_applier as _post_to_applier_sidecar,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])

_DEFAULT_QUERY = """\
PREFIX schema: <http://schema.org/>
SELECT ?repo WHERE {
  ?repo a schema:SoftwareSourceCode .
}
ORDER BY ?repo
"""


def _slugify(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_]", "", s)
    return s or "open_pulse_sparql"


@router.post("/sparql/query", dependencies=[Depends(require_auth)])
async def run_sparql(
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Run a SELECT against the SPARQL endpoint and return the bindings.

    Body (all optional):
      endpoint: SPARQL base URL or full /query path (defaults to settings.sparql_url)
      query: SPARQL string (defaults to the schema:SoftwareSourceCode select)
      auth_user / auth_password: optional Basic credentials
    """
    settings = get_settings()
    endpoint = (payload.get("endpoint") or settings.sparql_url).rstrip("/")
    if not endpoint.endswith("/query"):
        endpoint += "/query"
    query = payload.get("query") or _DEFAULT_QUERY

    # Per-request auth wins; otherwise fall back to the server-side
    # SPARQL_AUTH (parsed into user / password by the settings loader).
    user = (payload.get("auth_user") or "").strip() or settings.sparql_user
    pw = payload.get("auth_password")
    if pw is None or pw == "":
        pw = settings.sparql_password
    auth = (user, pw) if user and pw else None

    headers = {"Accept": "application/sparql-results+json"}
    async with httpx.AsyncClient(timeout=30.0) as c:
        resp = await c.get(
            endpoint,
            params={"query": query},
            headers=headers,
            auth=auth,
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"SPARQL endpoint returned HTTP {resp.status_code}: {resp.text[:200]}",
        )
    body = resp.json()
    bindings = (body.get("results") or {}).get("bindings") or []
    repos = sorted(
        {
            r["repo"]["value"]
            for r in bindings
            if isinstance(r.get("repo", {}).get("value"), str)
            and r["repo"]["value"].startswith(("http://", "https://"))
        }
    )
    return {"count": len(repos), "repos": repos}


@router.post("/build", dependencies=[Depends(require_auth)])
def build(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """Wrap a repo list in the GrimoireLab projects.json envelope (no I/O)."""
    repos = list(payload.get("repos") or [])
    title = payload.get("title") or "Open Pulse SPARQL"
    slug = _slugify(title)
    return {slug: {"meta": {"title": title}, "git": repos}}


@router.post("/apply", dependencies=[Depends(require_auth)])
async def apply(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """Forward a projects.json payload to the applier sidecar.

    Body:
      projects_json: dict — the full projects.json envelope to write
      applier_token: str — the APPLIER_AUTH bearer the sidecar expects
      applier_url: str — optional override (defaults to settings.applier_url)
    """
    settings = get_settings()
    # Per-request token (from the Settings page → localStorage) wins;
    # otherwise we fall back to the server-side APPLIER_AUTH the hub
    # container was started with. Only fail if neither is set.
    token = (payload.get("applier_token") or "").strip() or settings.applier_auth
    if not token:
        raise HTTPException(
            status_code=400,
            detail="No applier credentials available. Set APPLIER_AUTH in your "
            ".env (server-side default) or paste a token under Settings "
            "→ GrimoireLab projects key for a remote deployment.",
        )
    projects_json = payload.get("projects_json")
    if not isinstance(projects_json, dict) or not projects_json:
        raise HTTPException(
            status_code=400, detail="projects_json must be a non-empty object"
        )
    url = (payload.get("applier_url") or settings.applier_url).rstrip("/") + "/apply"
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=60.0) as c:
        resp = await c.post(url, json=projects_json, headers=headers)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"applier returned HTTP {resp.status_code}: {resp.text[:200]}",
        )
    return resp.json()


# ── Templates + facets ──────────────────────────────────────────────────────


@router.get("/templates", dependencies=[Depends(require_auth)])
def list_templates() -> dict[str, Any]:
    """Return the built-in SPARQL query library (parameterised templates)."""
    return {"templates": builtin_query_dicts()}


_FACETS_CACHE: dict[str, Any] = {"at": 0.0, "data": None, "endpoint": ""}
_FACETS_TTL = (
    30.0  # seconds — cheap enough to refresh, expensive enough to skip on every nav
)


async def _run_sparql(
    client: httpx.AsyncClient,
    endpoint: str,
    query: str,
    auth: tuple[str, str] | None = None,
) -> dict[str, Any]:
    url = endpoint.rstrip("/")
    if not url.endswith("/query"):
        url += "/query"
    resp = await client.get(
        url,
        params={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        auth=auth,
        timeout=30.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()


@router.get("/facets", dependencies=[Depends(require_auth)])
async def facets(refresh: bool = False) -> dict[str, Any]:
    """Enumerate every facet's distinct values + counts.

    Runs one SPARQL ``SELECT … GROUP BY ?value`` per facet against the
    configured store (in parallel) and shapes the result for the UI. Cached
    for ``_FACETS_TTL`` seconds; pass ``?refresh=true`` to bypass.
    """
    settings = get_settings()
    endpoint = settings.sparql_url
    now = time.time()
    if (
        not refresh
        and _FACETS_CACHE["data"] is not None
        and _FACETS_CACHE["endpoint"] == endpoint
        and now - _FACETS_CACHE["at"] < _FACETS_TTL
    ):
        return _FACETS_CACHE["data"]

    async def _facet_values(client: httpx.AsyncClient, facet) -> dict[str, Any]:
        try:
            body = await _run_sparql(client, endpoint, facet.values_query)
        except Exception as exc:  # noqa: BLE001 — surface the error per facet
            return {
                "key": facet.key,
                "label": facet.label,
                "description": facet.description,
                "predicate_path": facet.predicate_path,
                "error": str(exc),
                "values": [],
            }
        bindings = (body.get("results") or {}).get("bindings") or []
        values: list[dict[str, Any]] = []
        for b in bindings:
            cell = b.get("value") or {}
            cnt = b.get("count") or {}
            try:
                count = int(cnt.get("value") or 0)
            except (TypeError, ValueError):
                count = 0
            values.append(
                {
                    "value": cell.get("value", ""),
                    "type": cell.get("type", "literal"),
                    "count": count,
                }
            )
        return {
            "key": facet.key,
            "label": facet.label,
            "description": facet.description,
            "predicate_path": facet.predicate_path,
            "values": values,
        }

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *(_facet_values(client, f) for f in FACETS),
            return_exceptions=False,
        )
        # Decorate Wikidata IRIs (e.g. ``http://www.wikidata.org/entity/Q428691``)
        # with their human-readable label. We batch every Q-id we
        # found across all facets into one ``wbgetentities`` call and
        # cache the result in process memory — 15 disciplines + a
        # handful of others means one HTTP round-trip on the first
        # uncached load, then nothing.
        await _decorate_wikidata_labels(client, results)

    payload = {"endpoint": endpoint, "facets": list(results)}
    _FACETS_CACHE["data"] = payload
    _FACETS_CACHE["at"] = now
    _FACETS_CACHE["endpoint"] = endpoint
    return payload


# In-process cache of Wikidata Q-id → English label. Discipline
# values in our SPARQL store are stored as opaque Wikidata IRIs;
# resolving them to a label here keeps the data clean (we don't
# duplicate Wikidata into our triples) without making the UI render
# raw Q-numbers.
_WIKIDATA_LABEL_CACHE: dict[str, str] = {}
_WIKIDATA_IRI_PREFIX = "http://www.wikidata.org/entity/"


async def _decorate_wikidata_labels(
    client: httpx.AsyncClient,
    facets_payload: list[dict[str, Any]],
) -> None:
    """For every facet value whose ``value`` is a Wikidata entity IRI,
    add a ``label`` field with its English label. Cached.
    """
    # Collect every Q-id that needs resolving, minus what we already
    # have in the cache.
    needed: set[str] = set()
    for facet in facets_payload:
        for v in facet.get("values") or []:
            iri = v.get("value") or ""
            if iri.startswith(_WIKIDATA_IRI_PREFIX):
                qid = iri[len(_WIKIDATA_IRI_PREFIX) :]
                if qid and qid not in _WIKIDATA_LABEL_CACHE:
                    needed.add(qid)
    if needed:
        # The MediaWiki API allows up to 50 IDs per call. Batch.
        chunks = [list(needed)[i : i + 50] for i in range(0, len(needed), 50)]
        for chunk in chunks:
            try:
                # Wikidata's API rejects requests whose User-Agent
                # doesn't identify the tool + contact (per their bot
                # policy at meta.wikimedia.org/wiki/User-Agent_policy).
                # httpx's default ``python-httpx/X.Y`` returns 403
                # against the action API — give it a proper UA.
                r = await client.get(
                    "https://www.wikidata.org/w/api.php",
                    params={
                        "action": "wbgetentities",
                        "ids": "|".join(chunk),
                        "props": "labels",
                        "languages": "en",
                        "format": "json",
                    },
                    timeout=8.0,
                    headers={
                        "User-Agent": (
                            "open-pulse-hub/1.0 "
                            "(+https://github.com/sdsc-ordes/open-pulse; "
                            "open-pulse@epfl.ch) httpx"
                        ),
                        "Accept": "application/json",
                    },
                )
                if r.status_code != 200:
                    continue
                payload = r.json()
            except Exception:  # noqa: BLE001 — never fatal
                continue
            for qid, ent in (payload.get("entities") or {}).items():
                label = (
                    ((ent.get("labels") or {}).get("en") or {}).get("value")
                ) or qid
                _WIKIDATA_LABEL_CACHE[qid] = label

    # Now annotate every facet value in place.
    for facet in facets_payload:
        for v in facet.get("values") or []:
            iri = v.get("value") or ""
            if iri.startswith(_WIKIDATA_IRI_PREFIX):
                qid = iri[len(_WIKIDATA_IRI_PREFIX) :]
                lbl = _WIKIDATA_LABEL_CACHE.get(qid)
                if lbl:
                    v["label"] = lbl


@router.post("/build-from-filters", dependencies=[Depends(require_auth)])
async def build_from_filters(
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Compose a SPARQL query from a facet-selection map and run it.

    Body shape::

        {
          "selections": {"organization": ["sdsc-ordes"], "license": ["..."]},
          "endpoint": "http://sparql-proxy:7878"   # optional
        }

    Returns the generated query, the matching repos, and the count.
    """
    settings = get_settings()
    selections = payload.get("selections") or {}
    if not isinstance(selections, dict):
        raise HTTPException(status_code=400, detail="selections must be an object")

    cleaned: dict[str, list[str]] = {}
    for key, values in selections.items():
        if not isinstance(values, list):
            continue
        cleaned[str(key)] = [str(v) for v in values if isinstance(v, (str, int, float))]

    query = build_filtered_query(cleaned)
    endpoint = (payload.get("endpoint") or settings.sparql_url).rstrip("/")
    # Same fallback rule as /sparql/query — body wins, otherwise pull
    # the credentials the hub container was started with (SPARQL_AUTH).
    user = (payload.get("auth_user") or "").strip() or settings.sparql_user
    pw = payload.get("auth_password")
    if pw is None or pw == "":
        pw = settings.sparql_password
    auth = (user, pw) if user and pw else None

    try:
        async with httpx.AsyncClient() as client:
            body = await _run_sparql(client, endpoint, query, auth=auth)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    bindings = (body.get("results") or {}).get("bindings") or []
    repos: list[str] = []
    for b in bindings:
        cell = b.get("repo") or {}
        v = cell.get("value")
        if isinstance(v, str) and v.startswith(("http://", "https://")):
            repos.append(v)
    repos = sorted(set(repos))

    return {
        "query": query,
        "selections": cleaned,
        "count": len(repos),
        "repos": repos,
    }


@router.post("/build-by-owner", dependencies=[Depends(require_auth)])
def build_by_owner(
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Build (and optionally apply) an owner-grouped projects.json.

    Body (all optional):
      include_unexplored: bool          — include unvisited Repo nodes too
      min_repos_per_owner: int          — drop owners with < N repos
      title_prefix: str                 — prepend to each group's display title
      apply: bool                       — also POST to the applier
      neo4j_password: str               — override server-side NEO4J_AUTH

    Returns ``{owners, repos, projects_json, applied, applier_response?}``.
    """
    import os

    settings = get_settings()

    pw = (payload.get("neo4j_password") or "").strip()
    if not pw:
        # Match the runner's parsing — NEO4J_AUTH is "user/password".
        raw = os.environ.get("NEO4J_AUTH", "")
        if "/" in raw:
            pw = raw.split("/", 1)[1]
    if not pw:
        raise HTTPException(
            status_code=400,
            detail="No Neo4j password available. Set NEO4J_AUTH in .env or "
            "include neo4j_password in the request body.",
        )

    include_unexplored = bool(payload.get("include_unexplored", False))
    min_repos = int(payload.get("min_repos_per_owner", 1) or 1)
    title_prefix = str(payload.get("title_prefix", "") or "")
    do_apply = bool(payload.get("apply", False))

    try:
        projects, total = build_owner_grouped_projects(
            neo4j_endpoint=settings.neo4j_url,
            neo4j_auth=("neo4j", pw),
            include_unexplored=include_unexplored,
            min_repos_per_owner=min_repos,
            title_prefix=title_prefix,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Neo4j query failed: {exc}"
        ) from exc

    response: dict[str, Any] = {
        "owners": len(projects),
        "repos": total,
        "projects_json": projects,
        "applied": False,
    }

    if do_apply and projects:
        token = settings.applier_auth
        if not token:
            raise HTTPException(
                status_code=400,
                detail="apply=true but APPLIER_AUTH is not set on the server "
                "and no override was provided.",
            )
        try:
            applier_resp = _post_to_applier_sidecar(
                applier_url=settings.applier_url,
                bearer_token=token,
                payload=projects,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        response["applied"] = True
        response["applier_response"] = applier_resp
    return response
