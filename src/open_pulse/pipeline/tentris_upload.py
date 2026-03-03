"""Tentris upload pipeline step (placeholder).

Actual upload logic will be added when the Tentris RDF ingestion path is
implemented.  For now the step logs execution and returns immediately.
"""

from __future__ import annotations

import logging

from open_pulse.services.container import ServiceContainer

logger = logging.getLogger(__name__)


def _services_from_context(context: dict[str, object]) -> ServiceContainer:
    services = context.get("services")
    if not isinstance(services, ServiceContainer):
        raise RuntimeError("Pipeline context missing ServiceContainer under 'services'.")
    return services


def run_tentris_upload(context: dict[str, object]) -> None:
    """Upload RDF triples into the Tentris SPARQL store."""
    services = _services_from_context(context)
    services.tentris.upload(context)
    logger.info("tentris_upload: placeholder step executed via service client")
