"""Task contracts and built-in task helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


class AnalysisTask(Protocol):
    """Contract for sequential analysis tasks."""

    name: str

    def run(self, context: dict[str, object]) -> None:
        """Run task logic against a shared execution context."""


@dataclass(frozen=True, slots=True)
class FunctionTask:
    """Small adapter to create tasks from callables."""

    name: str
    func: Callable[[dict[str, object]], None]

    def run(self, context: dict[str, object]) -> None:
        self.func(context)
