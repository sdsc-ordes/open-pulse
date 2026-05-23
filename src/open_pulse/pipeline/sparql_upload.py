"""SPARQL-store upload pipeline step.

Reads the per-repo JSON-LD files produced by the ``metadata_extractor`` step
and uploads each via :meth:`SparqlStoreService.upload`. Failures on individual
files are logged and counted, not propagated — one bad file shouldn't kill
a batch. The runner-level retry will re-execute the whole step if *every*
file fails (zero successes).

Named-graph selection (highest precedence first):
    1. ``named_graph`` — explicit literal URI from the quest YAML
    2. ``auto_named_graph: true`` + ``runtime`` — derives a monthly URI
       of the form ``{base}/{YYYY-MM}/{runtime}``
    3. Neither set — triples land in the default graph

When ``publish_to_default`` is true (or auto for the hybrid runtime),
the named graph is then ``COPY``-d to the default graph so clients that
don't pick a specific GRAPH see the latest snapshot.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from open_pulse.services.container import ServiceContainer
from open_pulse.services.sparql_store import (
    DEFAULT_RUNTIME_PUBLISHES_TO_DEFAULT,
    derive_monthly_graph_uri,
)

logger = logging.getLogger(__name__)


def _services_from_context(context: dict[str, object]) -> ServiceContainer:
    services = context.get("services")
    if not isinstance(services, ServiceContainer):
        raise RuntimeError(
            "Pipeline context missing ServiceContainer under 'services'."
        )
    return services


def _resolve_named_graph(
    step_cfg: dict[str, object], *, runtime_fallback: str | None = None
) -> tuple[str | None, str | None]:
    """Return ``(named_graph_uri, runtime)`` from the step config.

    Resolution order: explicit ``named_graph`` literal wins; otherwise
    if ``auto_named_graph`` is true, derive from ``runtime`` (or the
    caller-supplied fallback for steps where runtime is implicit, like
    ``metadata_extractor``'s ``v2_agent_runtime``). When neither
    triggers, returns ``(None, runtime)``.
    """
    explicit = step_cfg.get("named_graph") or step_cfg.get("stream_named_graph")
    runtime = step_cfg.get("runtime") or runtime_fallback
    if explicit:
        return str(explicit), str(runtime) if runtime else None
    if bool(step_cfg.get("auto_named_graph", False)):
        if not runtime:
            raise ValueError(
                "auto_named_graph requires a runtime (set ``runtime`` on the step "
                "config, or rely on the step's runtime field)."
            )
        return derive_monthly_graph_uri(str(runtime)), str(runtime)
    return None, str(runtime) if runtime else None


def _should_publish_to_default(
    step_cfg: dict[str, object], runtime: str | None
) -> bool:
    """Resolve the tri-state ``publish_to_default`` flag.

    ``True`` / ``False`` is honored verbatim; ``None`` (the default)
    auto-publishes only for runtimes in
    ``DEFAULT_RUNTIME_PUBLISHES_TO_DEFAULT`` — currently just
    ``hybrid``.
    """
    explicit = step_cfg.get("publish_to_default")
    if isinstance(explicit, bool):
        return explicit
    return bool(runtime) and runtime in DEFAULT_RUNTIME_PUBLISHES_TO_DEFAULT


def run_sparql_upload(context: dict[str, object]) -> None:
    """Upload all metadata-json files into the SPARQL store."""
    services = _services_from_context(context)
    step_cfg = context.get("step_config", {})
    if not isinstance(step_cfg, dict):
        raise RuntimeError("Pipeline context 'step_config' must be a dict.")

    input_dir = Path(str(step_cfg.get("input_dir", ".quest-artifacts/metadata-json")))
    named_graph, runtime = _resolve_named_graph(step_cfg)

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
        "sparql_upload: success=%d failed=%d triples=%d (input_dir=%s graph=%s)",
        success,
        len(failed),
        triples_total,
        input_dir,
        named_graph or "default",
    )
    if success and named_graph and _should_publish_to_default(step_cfg, runtime):
        # COPY is atomic on the server side — clients querying without a
        # GRAPH clause flip to the new snapshot in a single transaction.
        try:
            services.sparql_store.copy_to_default(named_graph)
            logger.info("sparql_upload: published <%s> to default graph", named_graph)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "sparql_upload: publish_to_default failed (%s); the named "
                "graph is still up to date, only the default-graph mirror "
                "lags behind.",
                exc,
            )
    if success == 0 and failed:
        raise RuntimeError(
            f"sparql_upload: all {len(failed)} files failed; first failure: {failed[0]}"
        )
