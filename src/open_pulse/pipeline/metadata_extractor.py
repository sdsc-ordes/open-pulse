"""Metadata extractor pipeline step (placeholder).

Actual extraction logic will be added when the metadata extraction service
is implemented.  For now the step logs execution and returns immediately.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def run_metadata_extractor(context: dict[str, object]) -> None:
    """Extract metadata from graph-stored artefacts."""
    step_cfg = context.get("step_config", {})
    input_dir = step_cfg.get("input_dir") if isinstance(step_cfg, dict) else None
    output_dir = step_cfg.get("output_dir") if isinstance(step_cfg, dict) else None
    logger.info(
        "metadata_extractor: placeholder step executed (input_dir=%s, output_dir=%s)",
        input_dir,
        output_dir,
    )
