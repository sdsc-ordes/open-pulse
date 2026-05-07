"""SPARQL-store upload pipeline step.

Reads the per-repo JSON-LD files produced by the ``metadata_extractor`` step
and uploads each via :meth:`SparqlStoreService.upload`. Failures on individual
files are logged and counted, not propagated — one bad file shouldn't kill
a batch. The runner-level retry will re-execute the whole step if *every*
file fails (zero successes).

The ``named_graph`` step config sends every payload to the same named graph
URI; leave unset to write to the default graph.
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


def run_sparql_upload(context: dict[str, object]) -> None:
    """Upload all metadata-json files into the SPARQL store."""
    services = _services_from_context(context)
    step_cfg = context.get("step_config", {})
    if not isinstance(step_cfg, dict):
        raise RuntimeError("Pipeline context 'step_config' must be a dict.")

    input_dir = Path(str(step_cfg.get("input_dir", ".quest-artifacts/metadata-json")))
    named_graph = step_cfg.get("named_graph")
    if named_graph is not None:
        named_graph = str(named_graph)

    if not input_dir.is_dir():
        raise FileNotFoundError(
            f"sparql_upload: expected metadata-json directory at {input_dir} — "
            "did the metadata_extractor step run successfully?"
        )

    files = sorted(input_dir.glob("*.json"))
    if not files:
        logger.warning("sparql_upload: %s has no JSON files to upload", input_dir)
        return

    success = 0
    triples_total = 0
    failed: list[str] = []
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            triples = services.sparql_store.upload(payload, named_graph=named_graph)
            success += 1
            triples_total += triples
        except Exception as exc:  # noqa: BLE001
            logger.warning("sparql_upload: %s failed (%s)", path.name, exc)
            failed.append(path.name)

    logger.info(
        "sparql_upload: success=%d failed=%d triples=%d (input_dir=%s)",
        success,
        len(failed),
        triples_total,
        input_dir,
    )
    if success == 0 and failed:
        raise RuntimeError(
            f"sparql_upload: all {len(failed)} files failed; first failure: {failed[0]}"
        )
