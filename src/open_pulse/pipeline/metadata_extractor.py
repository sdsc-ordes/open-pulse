"""Metadata extractor pipeline step.

For each repo in the crawler-emitted graph, calls the gimie JSON-LD endpoint
on the git-metadata-extractor service and writes the response to a per-repo
file under ``output_dir`` (one ``<owner>__<repo>.json`` per repo).

Failures on individual repos are logged and counted, not propagated — one
flaky repo shouldn't kill a batch of 100. The runner-level retry will
re-execute the whole step if *every* repo fails (zero successes).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def _safe_filename(full_name: str) -> str:
    """Convert ``owner/repo`` to a filesystem-safe ``owner__repo.json``."""
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", full_name).strip("_")
    return f"{safe}.json"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def run_metadata_extractor(context: dict[str, object]) -> None:
    """Fetch JSON-LD from the metadata extractor for each repo in the crawler graph."""
    services = _services_from_context(context)
    step_cfg = context.get("step_config", {})
    if not isinstance(step_cfg, dict):
        raise RuntimeError("Pipeline context 'step_config' must be a dict.")

    input_dir = Path(str(step_cfg.get("input_dir", ".quest-artifacts/crawler-json")))
    input_filename = str(step_cfg.get("input_filename", "crawler-graph.json"))
    input_path = input_dir / input_filename
    output_dir = Path(str(step_cfg.get("output_dir", ".quest-artifacts/metadata-json")))
    force_refresh = bool(step_cfg.get("force_refresh", False))
    skip_existing = bool(step_cfg.get("skip_existing", True))
    max_repos = step_cfg.get("max_repos")
    mode = str(step_cfg.get("mode", "v2"))
    v2_agent_runtime = str(step_cfg.get("v2_agent_runtime", "rule_based"))
    v2_poll = float(step_cfg.get("v2_poll_interval_seconds", 2.0))
    v2_timeout = float(step_cfg.get("v2_timeout_seconds", 600.0))
    include_internal_fields = bool(step_cfg.get("include_internal_fields", False))
    # How many v2 submits run in flight at once. Mirrors the GME's
    # V2_MAX_CONCURRENT_AGENTS default of 6 — going higher than that
    # just makes requests queue server-side. ``1`` reverts to fully
    # sequential, useful for debugging single-repo failures.
    max_workers = int(step_cfg.get("max_workers", 6))
    if max_workers < 1:
        max_workers = 1
    if mode not in ("v1_gimie", "v2"):
        raise ValueError(
            f"metadata_extractor: unknown mode {mode!r}; expected 'v1_gimie' or 'v2'."
        )

    if not input_path.is_file():
        raise FileNotFoundError(
            f"metadata_extractor: expected crawler graph at {input_path} — "
            "did the crawler step run successfully?"
        )

    graph = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(graph, dict):
        raise RuntimeError(
            f"metadata_extractor: {input_path} did not contain a JSON object."
        )

    repos: dict[str, Any] = graph.get("repos") or {}
    if not repos:
        logger.warning(
            "metadata_extractor: crawler graph at %s has no repos to process",
            input_path,
        )
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    success = 0
    skipped = 0
    failed: list[str] = []
    # Worker threads update ``success`` / ``failed`` so the writes need to
    # be serialised. ``_atomic_write_json`` already targets a unique
    # per-repo path so concurrent writes don't clash on disk.
    counters_lock = threading.Lock()
    # `max_repos = 0` is an explicit "no limit" sentinel: it would be silly
    # for the runner to extract zero repos. Anything > 0 is a hard cap.
    cap: int | None = int(max_repos) if max_repos is not None else None
    if cap is not None and cap <= 0:
        cap = None

    # Build the work list up front so the cap + skip_existing filter
    # applies before we fan out to workers. Doing it inline-inside the
    # pool would let ``skipped`` rows still consume a worker slot.
    candidates: list[str] = []
    for i, full_name in enumerate(repos.keys()):
        if cap is not None and i >= cap:
            logger.info("metadata_extractor: hit max_repos=%s, stopping early", cap)
            break
        out_path = output_dir / _safe_filename(full_name)
        if skip_existing and out_path.is_file():
            skipped += 1
            continue
        candidates.append(full_name)

    def _process(full_name: str) -> None:
        nonlocal success
        out_path = output_dir / _safe_filename(full_name)
        try:
            if mode == "v2":
                payload = services.metadata_extractor.extract_repo_jsonld_v2(
                    full_name,
                    agent_runtime=v2_agent_runtime,
                    include_internal_fields=include_internal_fields,
                    poll_interval=v2_poll,
                    timeout=v2_timeout,
                )
            else:  # v1_gimie
                payload = services.metadata_extractor.fetch_repo_jsonld(
                    full_name, force_refresh=force_refresh
                )
            _atomic_write_json(out_path, payload)
            with counters_lock:
                success += 1
            logger.info(
                "metadata_extractor [%s]: %s -> %s",
                mode,
                full_name,
                out_path.name,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("metadata_extractor: %s failed (%s)", full_name, exc)
            with counters_lock:
                failed.append(full_name)

    if candidates:
        # ``as_completed`` drains the futures eagerly so any per-worker
        # exception (a runaway thread, a deadlock in the service client)
        # surfaces immediately instead of waiting for pool teardown. The
        # final ``list(...)`` is just to consume the generator — return
        # values are None, so we don't keep them.
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            list(as_completed(pool.submit(_process, fn) for fn in candidates))

    logger.info(
        "metadata_extractor: success=%d skipped=%d failed=%d (output_dir=%s)",
        success,
        skipped,
        len(failed),
        output_dir,
    )
    if success == 0 and not skipped and failed:
        # All attempted repos failed — fail the step so the runner can retry.
        raise RuntimeError(
            f"metadata_extractor: all {len(failed)} repos failed; "
            f"first failure: {failed[0]}"
        )
