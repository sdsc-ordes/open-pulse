"""Deterministic sequential orchestrator with checkpoint support."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from open_pulse.tasks import AnalysisTask


@dataclass(frozen=True, slots=True)
class OrchestrationError(Exception):
    """Error raised when a task fails during sequential execution."""

    failed_task: str
    completed_tasks: tuple[str, ...]

    def __str__(self) -> str:
        return (
            f"Task '{self.failed_task}' failed after completing "
            f"{len(self.completed_tasks)} task(s)."
        )


def _read_checkpoint(checkpoint_path: Path) -> list[str]:
    if not checkpoint_path.exists():
        return []

    raw = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    completed = raw.get("completed_tasks", [])
    if not isinstance(completed, list):
        raise ValueError("Invalid checkpoint format: completed_tasks must be a list.")
    return [str(name) for name in completed]


def _write_checkpoint(checkpoint_path: Path, completed_tasks: list[str]) -> None:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(
        json.dumps({"completed_tasks": completed_tasks}, indent=2) + "\n",
        encoding="utf-8",
    )


def run_sequential(
    tasks: tuple[AnalysisTask, ...],
    checkpoint_path: Path,
    *,
    resume: bool = False,
    initial_context: dict[str, object] | None = None,
) -> tuple[str, ...]:
    """Run tasks in order, writing checkpoint state after each success."""

    completed = _read_checkpoint(checkpoint_path) if resume else []
    completed_set = set(completed)
    context: dict[str, object] = {"checkpoint_path": str(checkpoint_path)}
    if initial_context:
        context.update(initial_context)

    for task in tasks:
        if task.name in completed_set:
            continue

        try:
            task.run(context)
        except Exception as exc:
            _write_checkpoint(checkpoint_path, completed)
            raise OrchestrationError(
                failed_task=task.name,
                completed_tasks=tuple(completed),
            ) from exc

        completed.append(task.name)
        completed_set.add(task.name)
        _write_checkpoint(checkpoint_path, completed)

    return tuple(completed)
