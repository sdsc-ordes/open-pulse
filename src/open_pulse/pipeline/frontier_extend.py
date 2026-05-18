"""Frontier-extend pipeline step.

Reads an existing crawler-graph.json (typically the output of a prior
``crawler`` step), computes the *frontier* — repos mentioned as dependents
but never explored — and submits a fresh crawl seeded with those nodes.
The resulting subgraph is merged back into the canonical graph file so
downstream steps (neo4j_upload, metadata_extractor, sparql_upload) see a
graph that's exactly one ring wider.

This is cheaper than re-running the full crawler with ``max_rounds++``:
the previous run's interior nodes are not re-explored, only the boundary
is filled in.

Disabled by default. Enable it explicitly in a quest YAML to run after
``crawler`` (or standalone, with ``crawler.enabled: false``, to extend an
existing graph in place).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from open_pulse.services.container import ServiceContainer

logger = logging.getLogger(__name__)


def _services_from_context(context: dict[str, object]) -> ServiceContainer:
    services = context.get("services")
    if not isinstance(services, ServiceContainer):
        raise RuntimeError(
            "Pipeline context missing ServiceContainer under 'services'."
        )
    return services


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _compute_frontier(graph: dict[str, Any]) -> list[str]:
    """Return repos referenced as dependents but not yet explored.

    A repo is on the frontier if it appears in some other repo's
    ``dependents`` list AND it is either absent from ``repos`` entirely or
    present with ``is_explored != True``. Sorted to make seed order
    deterministic across runs.
    """
    repos = graph.get("repos") or {}
    known = set(repos.keys())
    explored = {
        k for k, v in repos.items() if isinstance(v, dict) and v.get("is_explored")
    }

    mentioned: set[str] = set()
    for r in repos.values():
        if not isinstance(r, dict):
            continue
        for tgt in r.get("dependents") or []:
            if isinstance(tgt, str) and tgt:
                mentioned.add(tgt)

    return sorted((mentioned - known) | ((mentioned & known) - explored))


def _merge_node(base: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Merge two node payloads.

    Strategy:
    - If the new payload has ``is_explored=True`` and the base doesn't,
      take the new payload as the authoritative version (carries the
      richer metadata from the actual exploration).
    - Either way, union list-valued edge fields (``dependents``,
      ``dependencies``, ``contributors``) so we don't lose edges either
      side had recorded.
    """
    out = dict(base)
    base_explored = bool(base.get("is_explored"))
    new_explored = bool(new.get("is_explored"))
    if new_explored and not base_explored:
        out.update(new)
    elif new_explored and base_explored:
        # Both explored — keep base scalars but refresh timestamp if newer.
        ts_b = base.get("exploration_timestamp") or ""
        ts_n = new.get("exploration_timestamp") or ""
        if ts_n > ts_b:
            out.update(new)

    for key in ("dependents", "dependencies", "contributors"):
        old_list = base.get(key) or []
        new_list = new.get(key) or []
        if old_list or new_list:
            seen: set[str] = set()
            merged: list[str] = []
            for item in list(old_list) + list(new_list):
                if isinstance(item, str) and item not in seen:
                    seen.add(item)
                    merged.append(item)
            out[key] = merged
    return out


def _merge_graphs(
    base: dict[str, Any], new: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, int]]:
    """Merge ``new`` graph into ``base``; return (merged, stats)."""
    merged: dict[str, Any] = {}
    stats = {"added_users": 0, "added_orgs": 0, "added_repos": 0, "updated_repos": 0}
    section_to_stat = {
        "users": "added_users",
        "orgs": "added_orgs",
        "repos": "added_repos",
    }
    for section in ("users", "orgs", "repos"):
        b = dict(base.get(section) or {})
        n = new.get(section) or {}
        for k, v_new in n.items():
            if k not in b:
                b[k] = v_new
                stats[section_to_stat[section]] += 1
            else:
                if isinstance(v_new, dict) and isinstance(b[k], dict):
                    b[k] = _merge_node(b[k], v_new)
                    if section == "repos":
                        stats["updated_repos"] += 1
        merged[section] = b
    return merged, stats


_CRAWL_BODY_FIELDS = (
    "max_rounds",
    "crawl_dependencies",
    "crawl_dependents",
    "min_stars",
    "max_dependents",
    "max_contributors",
    "batch_size",
)


def run_frontier_extend(context: dict[str, object]) -> None:
    """Extend an existing crawler graph by one ring of dependents."""
    services = _services_from_context(context)
    step_cfg = context.get("step_config", {})
    if not isinstance(step_cfg, dict):
        raise RuntimeError("Pipeline context 'step_config' must be a dict.")

    input_dir = Path(str(step_cfg.get("input_dir", ".quest-artifacts/crawler-json")))
    input_filename = str(step_cfg.get("input_filename", "crawler-graph.json"))
    input_path = input_dir / input_filename

    # Default: write back to the same file so downstream steps see the
    # extended graph at the canonical path. Override to keep the base
    # graph intact and inspect the merged version separately.
    output_dir = Path(str(step_cfg.get("output_dir") or input_dir))
    output_filename = str(step_cfg.get("output_filename") or input_filename)
    output_path = output_dir / output_filename

    if not input_path.exists():
        logger.warning(
            "frontier_extend: input graph %s does not exist — nothing to extend",
            input_path,
        )
        return

    existing = json.loads(input_path.read_text(encoding="utf-8"))
    frontier = _compute_frontier(existing)
    if not frontier:
        logger.info("frontier_extend: graph is closed (no frontier nodes) — no-op")
        return

    raw_cap = step_cfg.get("max_frontier_seeds")
    cap = int(raw_cap) if isinstance(raw_cap, int) and raw_cap > 0 else None
    if cap is not None and len(frontier) > cap:
        logger.info(
            "frontier_extend: capping frontier %d → %d (max_frontier_seeds)",
            len(frontier),
            cap,
        )
        frontier = frontier[:cap]

    body: dict[str, Any] = {"seeds": frontier}
    for field in _CRAWL_BODY_FIELDS:
        if field in step_cfg:
            body[field] = step_cfg[field]
    body.setdefault("max_rounds", 1)
    body.setdefault("crawl_dependents", True)

    poll_interval = float(step_cfg.get("poll_interval_seconds", 5.0))
    timeout = float(step_cfg.get("timeout_seconds", 3600.0))

    job_id = services.crawler.submit_crawl(body)
    logger.info(
        "frontier_extend: submitted job_id=%s frontier_seeds=%d max_rounds=%s",
        job_id,
        len(frontier),
        body["max_rounds"],
    )

    final = services.crawler.wait_for_completion(
        job_id,
        poll_interval=poll_interval,
        timeout=timeout,
    )
    logger.info(
        "frontier_extend: job %s completed (users=%s orgs=%s repos=%s)",
        job_id,
        final.get("users", 0),
        final.get("orgs", 0),
        final.get("repos", 0),
    )

    new_graph = services.crawler.get_graph(job_id)
    merged, stats = _merge_graphs(existing, new_graph)
    _atomic_write_json(output_path, merged)

    logger.info(
        "frontier_extend: merged graph written to %s "
        "(added users=%d orgs=%d repos=%d; updated repos=%d; total repos=%d)",
        output_path,
        stats["added_users"],
        stats["added_orgs"],
        stats["added_repos"],
        stats["updated_repos"],
        len(merged.get("repos") or {}),
    )
