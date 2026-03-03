"""Crawler pipeline step (placeholder).

Actual crawling logic will be added when the crawler microservice is
implemented.  For now the step logs execution and returns immediately.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def run_crawler(context: dict[str, object]) -> None:
    """Crawl source repositories for analysis data."""
    logger.info("crawler: placeholder step executed")
