"""Neo4j upload pipeline step.

Reads the crawler graph JSON written by the previous step and pushes its
nodes + edges into Neo4j via :meth:`Neo4jService.upload`. Idempotent at the
driver level (every Cypher write is ``MERGE``), so retries are safe.

Config (from ``StepsConfig.neo4j_upload``):
- ``input_dir`` / ``input_filename`` — where to read the crawler graph from.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from open_pulse.services.container import ServiceContainer

logger = logging.getLogger(__name__)


def _services_from_context(context: dict[str, object]) -> ServiceContainer:
    services = context.get("services")
    if not isinstance(services, ServiceContainer):
        raise RuntimeError(
            "Pipeline context missing ServiceContainer under 'services'."
        )
    return services


def run_neo4j_upload(context: dict[str, object]) -> None:
    """Read the crawler graph JSON and upload it into Neo4j."""
    services = _services_from_context(context)
    step_cfg = context.get("step_config", {})
    if not isinstance(step_cfg, dict):
        raise RuntimeError("Pipeline context 'step_config' must be a dict.")

    input_dir = Path(str(step_cfg.get("input_dir", ".quest-artifacts/crawler-json")))
    input_filename = str(step_cfg.get("input_filename", "crawler-graph.json"))
    input_path = input_dir / input_filename

    if not input_path.is_file():
        raise FileNotFoundError(
            f"neo4j_upload: expected crawler graph at {input_path} — "
            "did the crawler step run successfully?"
        )

    graph = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(graph, dict):
        raise RuntimeError(
            f"neo4j_upload: {input_path} did not contain a JSON object."
        )

    counts = services.neo4j.upload(graph)
    logger.info(
        "neo4j_upload: ingested users=%s orgs=%s repos=%s from %s",
        counts.get("users", 0),
        counts.get("orgs", 0),
        counts.get("repos", 0),
        input_path,
    )
