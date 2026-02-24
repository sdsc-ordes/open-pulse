import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from open_pulse.cli import app
from open_pulse.orchestrator import run_sequential
from open_pulse.tasks import FunctionTask

runner = CliRunner()


# -- Typer CLI tests ---------------------------------------------------------


def test_cli_help_exits_cleanly() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_deploy_up_placeholder() -> None:
    result = runner.invoke(app, ["deploy", "up"])
    assert result.exit_code == 0
    assert "placeholder" in result.output


def test_quest_start_placeholder() -> None:
    result = runner.invoke(app, ["quest", "start"])
    assert result.exit_code == 0
    assert "placeholder" in result.output


def test_quest_run_step_placeholder() -> None:
    result = runner.invoke(app, ["quest", "run-step", "crawler"])
    assert result.exit_code == 0
    assert "placeholder" in result.output


def test_grimoire_prepare_config_placeholder() -> None:
    result = runner.invoke(app, ["grimoire", "prepare-config"])
    assert result.exit_code == 0
    assert "placeholder" in result.output


def test_grimoire_ui_placeholder() -> None:
    result = runner.invoke(app, ["grimoire", "ui"])
    assert result.exit_code == 0
    assert "placeholder" in result.output


def test_grimoire_install_watcher_placeholder() -> None:
    result = runner.invoke(app, ["grimoire", "install-watcher"])
    assert result.exit_code == 0
    assert "placeholder" in result.output


def test_health_placeholder() -> None:
    result = runner.invoke(app, ["health"])
    assert result.exit_code == 0
    assert "placeholder" in result.output


# -- Orchestrator tests (no CLI dependency) -----------------------------------


def test_orchestration_order_runs_in_sequence(tmp_path: Path) -> None:
    executed: list[str] = []

    def make_task(name: str) -> FunctionTask:
        return FunctionTask(name=name, func=lambda _ctx: executed.append(name))

    tasks = (make_task("one"), make_task("two"), make_task("three"))
    completed = run_sequential(tasks, tmp_path / "checkpoint.json")

    assert executed == ["one", "two", "three"]
    assert completed == ("one", "two", "three")


def test_failure_propagates(tmp_path: Path) -> None:
    from open_pulse.orchestrator import OrchestrationError

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


def test_shell_wrapper_forwards_args_and_exit_semantics() -> None:
    wrapper = Path(__file__).resolve().parents[1] / "tools" / "scripts" / "run-sequential.sh"
    content = wrapper.read_text(encoding="utf-8")

    assert "exec open-pulse run \"$@\"" in content
    assert "set -eu" in content
