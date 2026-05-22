"""Crawler pipeline step.

Drives the Open Pulse Crawler API end-to-end:

1. ``POST /api/v1/crawl`` with the per-job parameters from the step config.
2. Poll ``GET /api/v1/crawl/{job_id}`` until the job completes (handled by
   :meth:`CrawlerService.wait_for_completion`).
3. ``GET /api/v1/graph/{job_id}`` and atomically write the result to
   ``<output_dir>/<output_filename>``.

Failures (``CrawlerJobFailedError``, ``CrawlerJobTimeoutError``, network
errors) propagate up and are handled by the runner's retry wrapper. Each
retry submits a *new* job — the crawler does not support resumption of
previously-failed jobs.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from open_pulse.services.container import ServiceContainer

logger = logging.getLogger(__name__)


# Fields copied verbatim from step_cfg into the crawler's ``CrawlRequest``
# POST body. Keep in sync with ``CrawlerStepConfig`` — any field added
# there that the crawler also understands needs an entry here.
_BODY_FIELDS = (
    "seeds",
    "max_rounds",
    "crawl_dependencies",
    "crawl_dependents",
    "min_stars",
    "max_dependents",
    "max_contributors",
    "crawl_issues",
    "crawl_prs",
    "issue_max",
    "pr_max",
    "batch_size",
)


def _services_from_context(context: dict[str, object]) -> ServiceContainer:
    services = context.get("services")
    if not isinstance(services, ServiceContainer):
        raise RuntimeError(
            "Pipeline context missing ServiceContainer under 'services'."
        )
    return services


def _build_request(step_cfg: dict[str, object]) -> dict[str, object]:
    body: dict[str, object] = {}
    for field in _BODY_FIELDS:
        if field in step_cfg:
            body[field] = step_cfg[field]
    return body


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def run_crawler(context: dict[str, object]) -> None:
    """Submit a crawl job, wait for it, and persist the result graph."""
    services = _services_from_context(context)
    step_cfg = context.get("step_config", {})
    if not isinstance(step_cfg, dict):
        raise RuntimeError("Pipeline context 'step_config' must be a dict.")

    seeds = step_cfg.get("seeds") or []
    if not seeds:
        raise ValueError(
            "crawler step requires at least one seed in step_config['seeds']."
        )

    request = _build_request(step_cfg)
    poll_interval = float(step_cfg.get("poll_interval_seconds", 5.0))
    timeout = float(step_cfg.get("timeout_seconds", 3600.0))
    # GraphQL is the canonical endpoint per project convention — see
    # ``CrawlerStepConfig.use_graphql`` for the rationale. Quests can
    # opt out by setting ``use_graphql: false`` in their YAML.
    use_graphql = bool(step_cfg.get("use_graphql", True))

    job_id = services.crawler.submit_crawl(request, use_graphql=use_graphql)
    logger.info(
        "crawler: submitted job_id=%s seeds=%d endpoint=%s",
        job_id,
        len(seeds),
        "graphql" if use_graphql else "rest",
    )

    final = services.crawler.wait_for_completion(
        job_id,
        poll_interval=poll_interval,
        timeout=timeout,
    )
    logger.info(
        "crawler: job %s completed (users=%s orgs=%s repos=%s)",
        job_id,
        final.get("users", 0),
        final.get("orgs", 0),
        final.get("repos", 0),
    )

    graph = services.crawler.get_graph(job_id)

    output_dir = Path(str(step_cfg.get("output_dir", ".quest-artifacts/crawler-json")))
    output_filename = str(step_cfg.get("output_filename", "crawler-graph.json"))
    output_path = output_dir / output_filename
    _atomic_write_json(output_path, graph)

    logger.info("crawler: wrote graph to %s", output_path)
