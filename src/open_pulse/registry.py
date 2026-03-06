"""Task registry for sequential analysis execution."""

from __future__ import annotations

from open_pulse.tasks import AnalysisTask, FunctionTask


def _noop(_context: dict[str, object]) -> None:
    """Default placeholder task implementation."""


def get_registered_tasks() -> tuple[AnalysisTask, ...]:
    """Return tasks in deterministic execution order."""

    return (
        FunctionTask(name="collect-inputs", func=_noop),
        FunctionTask(name="compute-metrics", func=_noop),
        FunctionTask(name="publish-results", func=_noop),
    )
