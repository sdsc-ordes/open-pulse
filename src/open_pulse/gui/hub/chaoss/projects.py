"""GrimoireLab *project* → repository resolution for project-scoped CHAOSS
metrics.

A GrimoireLab ``projects.json`` groups data sources under a project name::

    {
      "info-eng": {
        "meta": {"title": "OP · information engineering"},
        "git":  ["https://github.com/owner/repo.git", ...]
      },
      ...
    }

We read the ``git`` source list and map each ``github.com`` URL to an
``owner/repo`` handle, so the existing per-repo metric compute can run across
every repo in a project and be aggregated. The file is the same one the
``projects-applier`` sidecar writes (and the ``/api/projects`` routes manage),
so project names here match the live GrimoireLab deployment 1:1.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Host path of the live projects.json. Same mount the hub already reads other
# index data from (``/data`` ← repo ``data/``). Overridable for tests / other
# deployments.
PROJECTS_JSON_PATH = Path(
    os.environ.get(
        "HUB_GRIMOIRE_PROJECTS_JSON",
        "/data/grimoirelab/projects-conf/projects.json",
    )
)

_GITHUB_RE = re.compile(r"github\.com[/:]+([^/]+)/(.+?)(?:\.git)?/?$", re.IGNORECASE)

# Tiny mtime-keyed cache — the file is small (~35 KB) but it's read on every
# project request, and the applier rewrites it only occasionally.
_cache: dict[str, Any] = {"mtime": None, "data": {}}


def _load() -> dict[str, Any]:
    try:
        mtime = PROJECTS_JSON_PATH.stat().st_mtime
    except OSError:
        return {}
    if _cache["mtime"] != mtime:
        try:
            _cache["data"] = json.loads(PROJECTS_JSON_PATH.read_text(encoding="utf-8"))
            _cache["mtime"] = mtime
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("projects.json read failed (%s): %s", PROJECTS_JSON_PATH, exc)
            return _cache["data"]  # serve last-good, if any
    return _cache["data"]


def _git_urls(block: dict[str, Any]) -> list[str]:
    """Every URL across the source-type lists of one project block.

    GrimoireLab keys the lists by backend (``git``, ``github``, ...); we accept
    any list-valued key so a repo counts whether it's listed under ``git`` or
    ``github``. ``meta`` (the title dict) is skipped.
    """
    urls: list[str] = []
    for key, val in block.items():
        if key == "meta" or not isinstance(val, list):
            continue
        urls.extend(u for u in val if isinstance(u, str))
    return urls


def _to_full(url: str) -> str | None:
    """``https://github.com/owner/repo.git`` → ``owner/repo`` (None if not github)."""
    m = _GITHUB_RE.search(url.strip())
    if not m:
        return None
    owner, repo = m.group(1), m.group(2).rstrip("/")
    return f"{owner}/{repo}" if owner and repo else None


def list_projects() -> list[dict[str, Any]]:
    """All defined projects with a title + github repo count."""
    data = _load()
    out: list[dict[str, Any]] = []
    for name, block in data.items():
        if not isinstance(block, dict):
            continue
        repos = {_to_full(u) for u in _git_urls(block)}
        repos.discard(None)
        meta = block.get("meta") or {}
        out.append(
            {
                "project": name,
                "title": (meta.get("title") if isinstance(meta, dict) else None) or name,
                "repo_count": len(repos),
            }
        )
    out.sort(key=lambda p: (-p["repo_count"], p["project"]))
    return out


def resolve_project_repos(project: str) -> list[str] | None:
    """``owner/repo`` handles for a project, or ``None`` if it isn't defined.

    Deduped + sorted for stable output. Non-github sources are dropped (the
    per-repo CHAOSS compute is github-keyed today).
    """
    data = _load()
    block = data.get(project)
    if not isinstance(block, dict):
        return None
    repos = {_to_full(u) for u in _git_urls(block)}
    repos.discard(None)
    return sorted(repos)


# ── repo discovery: what the CHAOSS frontend should offer / compare ─────────
# These power the landing's repo picker (only repos with data) and the
# front-page comparison overview. Both lean on one cheap aggregation over the
# GrimoireLab git-enriched indices.
_GIT_INDEX = "/git_*_enriched/_search"


def _indexed_origins(size: int = 1000) -> dict[str, int]:
    """``{owner/repo: commit_count}`` for every github origin GrimoireLab has
    ingested into OpenSearch. Empty when the store is unreachable."""
    from ..knowledge import opensearch as os_mod  # lazy — avoid import weight

    body = {"size": 0, "aggs": {"o": {"terms": {"field": "origin", "size": size}}}}
    res = os_mod._post(_GIT_INDEX, body)
    if not res:
        return {}
    out: dict[str, int] = {}
    for b in ((res.get("aggregations") or {}).get("o") or {}).get("buckets") or []:
        full = _to_full(str(b.get("key") or ""))
        if full:
            out[full] = int(b.get("doc_count") or 0)
    return out


def available_repos() -> list[str]:
    """Repos the CHAOSS picker should suggest — those in the GrimoireLab git
    index ∪ any project. Sorted by index activity (commits desc), then name,
    so the most-data-rich repos surface first. Cached briefly."""
    from ..knowledge import qdrant  # lazy

    def _gather() -> list[str]:
        indexed = _indexed_origins()
        repos = set(indexed)
        for p in list_projects():
            repos.update(resolve_project_repos(p["project"]) or [])
        return sorted(repos, key=lambda r: (-indexed.get(r, 0), r))

    return qdrant.cached_panel("chaoss_repos", "*", _gather)


def repo_overview(limit: int = 25) -> list[dict[str, Any]]:
    """Top repos by commit volume for the front-page comparison — one OS
    aggregation yielding commits + contributors + last-activity per repo."""
    from ..knowledge import opensearch as os_mod, qdrant  # lazy

    limit = max(1, min(100, int(limit or 25)))

    def _gather() -> list[dict[str, Any]]:
        body = {
            "size": 0,
            "aggs": {
                "repos": {
                    "terms": {
                        "field": "origin",
                        "size": limit,
                        "order": {"_count": "desc"},
                    },
                    "aggs": {
                        # author_uuid is GrimoireLab's identity-merged
                        # contributor key; author_name has no .keyword
                        # sub-field here (cardinality on it returns 0).
                        "contributors": {"cardinality": {"field": "author_uuid"}},
                        "last": {"max": {"field": "grimoire_creation_date"}},
                    },
                }
            },
        }
        res = os_mod._post(_GIT_INDEX, body)
        if not res:
            return []
        out: list[dict[str, Any]] = []
        buckets = ((res.get("aggregations") or {}).get("repos") or {}).get("buckets") or []
        for b in buckets:
            full = _to_full(str(b.get("key") or ""))
            if not full:
                continue
            out.append({
                "full": full,
                "commits": int(b.get("doc_count") or 0),
                "contributors": int((b.get("contributors") or {}).get("value") or 0),
                "last_activity": ((b.get("last") or {}).get("value_as_string") or "")[:10],
            })
        return out

    return qdrant.cached_panel("chaoss_overview", str(limit), _gather)
