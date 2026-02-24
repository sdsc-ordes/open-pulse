"""Tentris upload pipeline step (placeholder).

Actual upload logic will be added when the Tentris RDF ingestion path is
implemented.  For now the step logs execution and returns immediately.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def run_tentris_upload(context: dict[str, object]) -> None:
    """Upload RDF triples into the Tentris SPARQL store."""
    logger.info("tentris_upload: placeholder step executed")
