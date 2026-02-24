"""Neo4j upload pipeline step (placeholder).

Actual upload logic will be added when the graph ingestion service is
implemented.  For now the step logs execution and returns immediately.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def run_neo4j_upload(context: dict[str, object]) -> None:
    """Upload crawled data into the Neo4j knowledge graph."""
    logger.info("neo4j_upload: placeholder step executed")
