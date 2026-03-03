"""Metadata extractor pipeline step (placeholder).

Actual extraction logic will be added when the metadata extraction service
is implemented.  For now the step logs execution and returns immediately.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def run_metadata_extractor(context: dict[str, object]) -> None:
    """Extract metadata from graph-stored artefacts."""
    logger.info("metadata_extractor: placeholder step executed")
