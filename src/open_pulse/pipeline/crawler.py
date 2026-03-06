"""Crawler pipeline step (placeholder).

Actual crawling logic will be added when the crawler microservice is
implemented.  For now the step logs execution and returns immediately.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def run_crawler(context: dict[str, object]) -> None:
    """Crawl source repositories for analysis data."""
    step_cfg = context.get("step_config", {})
    output_dir = step_cfg.get("output_dir") if isinstance(step_cfg, dict) else None
    logger.info(
        "crawler: placeholder step executed (output_dir=%s)",
        output_dir,
    )
