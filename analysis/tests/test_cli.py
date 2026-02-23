import json
from pathlib import Path

import pytest

from openpulse_analysis import cli
from openpulse_analysis.orchestrator import run_sequential
from openpulse_analysis.tasks import FunctionTask


def test_cli_help_exits_cleanly() -> None:
    try:
        cli.main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0


def test_orchestration_order_runs_in_sequence(tmp_path: Path) -> None:
    executed: list[str] = []

    def make_task(name: str) -> FunctionTask:
        return FunctionTask(name=name, func=lambda _ctx: executed.append(name))

    tasks = (make_task("one"), make_task("two"), make_task("three"))
    completed = run_sequential(tasks, tmp_path / "checkpoint.json")

    assert executed == ["one", "two", "three"]
    assert completed == ("one", "two", "three")


def test_failure_propagates_with_non_zero_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def ok(_ctx: dict[str, object]) -> None:
        return None

    def fail(_ctx: dict[str, object]) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(
        cli,
        "get_registered_tasks",
        lambda: (
            FunctionTask(name="ok-step", func=ok),
            FunctionTask(name="failing-step", func=fail),
        ),
    )

    exit_code = cli.main(
        ["run", "--checkpoint-path", str(tmp_path / "checkpoint.json")]
    )
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "Run failed on task: failing-step" in output


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


def test_list_tasks_and_doctor_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "get_registered_tasks",
        lambda: (
            FunctionTask(name="alpha", func=lambda _ctx: None),
            FunctionTask(name="beta", func=lambda _ctx: None),
        ),
    )

    list_exit = cli.main(["list-tasks"])
    list_output = capsys.readouterr().out
    doctor_exit = cli.main(
        ["doctor", "--checkpoint-path", str(tmp_path / "checkpoint.json")]
    )
    doctor_output = capsys.readouterr().out

    assert list_exit == 0
    assert list_output.splitlines() == ["alpha", "beta"]
    assert doctor_exit == 0
    assert "OK:" in doctor_output


def test_shell_wrapper_forwards_args_and_exit_semantics() -> None:
    wrapper = Path(__file__).resolve().parents[1] / "scripts" / "run-sequential.sh"
    content = wrapper.read_text(encoding="utf-8")

    assert "exec openpulse-analysis run \"$@\"" in content
    assert "set -eu" in content
