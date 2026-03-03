"""Tests for the sequential orchestrator (no CLI dependency)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from open_pulse.orchestrator import OrchestrationError, run_sequential
from open_pulse.tasks import FunctionTask


def test_orchestration_order_runs_in_sequence(tmp_path: Path) -> None:
    executed: list[str] = []

    def make_task(name: str) -> FunctionTask:
        return FunctionTask(name=name, func=lambda _ctx: executed.append(name))

    tasks = (make_task("one"), make_task("two"), make_task("three"))
    completed = run_sequential(tasks, tmp_path / "checkpoint.json")

    assert executed == ["one", "two", "three"]
    assert completed == ("one", "two", "three")


def test_failure_propagates(tmp_path: Path) -> None:
    def ok(_ctx: dict[str, object]) -> None:
        return None

    def fail(_ctx: dict[str, object]) -> None:
        raise RuntimeError("boom")

    tasks = (
        FunctionTask(name="ok-step", func=ok),
        FunctionTask(name="failing-step", func=fail),
    )

    with pytest.raises(OrchestrationError, match="failing-step"):
        run_sequential(tasks, tmp_path / "checkpoint.json")


def test_resume_continues_from_next_step(tmp_path: Path) -> None:
    executed: list[str] = []
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(
        json.dumps({"completed_tasks": ["first"]}),
        encoding="utf-8",
    )

    tasks = (
        FunctionTask(name="first", func=lambda _ctx: executed.append("first")),
        FunctionTask(name="second", func=lambda _ctx: executed.append("second")),
        FunctionTask(name="third", func=lambda _ctx: executed.append("third")),
    )
    completed = run_sequential(tasks, checkpoint, resume=True)

    assert executed == ["second", "third"]
    assert completed == ("first", "second", "third")


def test_checkpoint_file_written_after_each_step(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.json"

    tasks = (
        FunctionTask(name="a", func=lambda _ctx: None),
        FunctionTask(name="b", func=lambda _ctx: None),
    )
    run_sequential(tasks, checkpoint)

    data = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert data["completed_tasks"] == ["a", "b"]


def test_checkpoint_saved_on_failure(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.json"

    def fail(_ctx: dict[str, object]) -> None:
        raise RuntimeError("boom")

    tasks = (
        FunctionTask(name="ok", func=lambda _ctx: None),
        FunctionTask(name="bad", func=fail),
    )

    with pytest.raises(OrchestrationError):
        run_sequential(tasks, checkpoint)

    data = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert data["completed_tasks"] == ["ok"]


def test_empty_task_list(tmp_path: Path) -> None:
    completed = run_sequential((), tmp_path / "checkpoint.json")
    assert completed == ()


def test_initial_context_is_passed_to_tasks(tmp_path: Path) -> None:
    received: dict[str, object] = {}

    def capture(ctx: dict[str, object]) -> None:
        received.update(ctx)

    tasks = (FunctionTask(name="capture", func=capture),)
    run_sequential(
        tasks,
        tmp_path / "checkpoint.json",
        initial_context={"services": "service-container"},
    )

    assert received["services"] == "service-container"


def test_shell_wrapper_forwards_args_and_exit_semantics() -> None:
    wrapper = Path(__file__).resolve().parents[1] / "tools" / "scripts" / "run-sequential.sh"
    content = wrapper.read_text(encoding="utf-8")

    assert "exec open-pulse run \"$@\"" in content
    assert "set -eu" in content
