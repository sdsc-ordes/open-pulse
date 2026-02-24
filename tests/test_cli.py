import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from open_pulse.cli import app
from open_pulse.commands import deploy as deploy_mod
from open_pulse.orchestrator import run_sequential
from open_pulse.pipeline.config import QuestFileConfig
from open_pulse.pipeline.runner import (
    STEP_NAMES,
    build_tasks,
    load_config,
    run_pipeline,
    run_single_step,
)
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


# -- Deploy command tests ----------------------------------------------------


def test_deploy_up_no_docker_exits_1() -> None:
    with patch.object(deploy_mod, "_docker_available", return_value=False):
        result = runner.invoke(app, ["deploy", "up", "--profile", "default"])
    assert result.exit_code == 1
    assert "Docker" in result.output


def test_deploy_up_with_profile_flag(tmp_path: Path) -> None:
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("FOO=bar", encoding="utf-8")

    with (
        patch.object(deploy_mod, "_docker_available", return_value=True),
        patch.object(deploy_mod, "_find_project_root", return_value=tmp_path),
        patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
    ):
        result = runner.invoke(app, ["deploy", "up", "--profile", "analysis"])

    assert result.exit_code == 0
    cmd = mock_run.call_args[0][0]
    assert "--profile" in cmd
    assert "analysis" in cmd
    assert "up" in cmd
    assert "-d" in cmd


def test_deploy_up_creates_env_from_template(tmp_path: Path) -> None:
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}", encoding="utf-8")
    template_dir = tmp_path / "infra" / "env"
    template_dir.mkdir(parents=True)
    (template_dir / ".env.example").write_text("A=1\nB=2\n", encoding="utf-8")

    with (
        patch.object(deploy_mod, "_docker_available", return_value=True),
        patch.object(deploy_mod, "_find_project_root", return_value=tmp_path),
        patch("subprocess.run", return_value=MagicMock(returncode=0)),
    ):
        result = runner.invoke(app, ["deploy", "up", "--profile", "default"])

    assert result.exit_code == 0
    created = tmp_path / ".env"
    assert created.is_file()
    assert created.read_text(encoding="utf-8") == "A=1\nB=2\n"


def test_deploy_down_runs_compose_down(tmp_path: Path) -> None:
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}", encoding="utf-8")

    with (
        patch.object(deploy_mod, "_docker_available", return_value=True),
        patch.object(deploy_mod, "_find_project_root", return_value=tmp_path),
        patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
    ):
        result = runner.invoke(app, ["deploy", "down"])

    assert result.exit_code == 0
    cmd = mock_run.call_args[0][0]
    assert "down" in cmd


def test_deploy_ps_runs_compose_ps(tmp_path: Path) -> None:
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}", encoding="utf-8")

    with (
        patch.object(deploy_mod, "_docker_available", return_value=True),
        patch.object(deploy_mod, "_find_project_root", return_value=tmp_path),
        patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
    ):
        result = runner.invoke(app, ["deploy", "ps"])

    assert result.exit_code == 0
    cmd = mock_run.call_args[0][0]
    assert "ps" in cmd


# -- Quest config tests ------------------------------------------------------


def test_quest_config_defaults() -> None:
    cfg = QuestFileConfig()
    assert cfg.quest.name == "default-quest"
    assert cfg.quest.retry.max_attempts == 3
    assert cfg.quest.retry.backoff_seconds == 5.0
    assert cfg.quest.logging.level == "INFO"
    assert cfg.quest.logging.file is None
    assert cfg.quest.steps.crawler.enabled is True
    assert cfg.quest.steps.neo4j_upload.endpoint == "bolt://localhost:7687"
    assert cfg.quest.steps.tentris_upload.endpoint == "http://localhost:7502"


def test_quest_config_from_yaml(tmp_path: Path) -> None:
    data = {
        "quest": {
            "name": "test-run",
            "retry": {"max_attempts": 2, "backoff_seconds": 1},
            "steps": {
                "crawler": {"enabled": False},
                "neo4j_upload": {"endpoint": "bolt://db:7687"},
            },
        }
    }
    config_file = tmp_path / "quest.yml"
    config_file.write_text(yaml.dump(data), encoding="utf-8")

    cfg = load_config(config_file)
    assert cfg.quest.name == "test-run"
    assert cfg.quest.retry.max_attempts == 2
    assert cfg.quest.steps.crawler.enabled is False
    assert cfg.quest.steps.neo4j_upload.endpoint == "bolt://db:7687"
    assert cfg.quest.steps.metadata_extractor.enabled is True


def test_quest_config_missing_file_uses_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "nonexistent.yml")
    assert cfg.quest.name == "default-quest"
    assert cfg.quest.steps.crawler.enabled is True


# -- Quest pipeline unit tests -----------------------------------------------


def test_build_tasks_all_enabled() -> None:
    cfg = QuestFileConfig()
    tasks = build_tasks(cfg)
    assert [t.name for t in tasks] == list(STEP_NAMES)


def test_build_tasks_skips_disabled() -> None:
    cfg = QuestFileConfig()
    cfg.quest.steps.crawler.enabled = False
    cfg.quest.steps.tentris_upload.enabled = False

    tasks = build_tasks(cfg)
    names = [t.name for t in tasks]
    assert "crawler" not in names
    assert "tentris_upload" not in names
    assert "neo4j_upload" in names
    assert "metadata_extractor" in names


def test_pipeline_runs_all_steps(tmp_path: Path) -> None:
    completed = run_pipeline(
        tmp_path / "quest.yml",
        checkpoint_dir=tmp_path,
    )
    assert completed == ("crawler", "neo4j_upload", "metadata_extractor", "tentris_upload")


def test_pipeline_resumes_from_checkpoint(tmp_path: Path) -> None:
    checkpoint = tmp_path / "default-quest.json"
    checkpoint.write_text(
        json.dumps({"completed_tasks": ["crawler", "neo4j_upload"]}),
        encoding="utf-8",
    )

    completed = run_pipeline(
        tmp_path / "quest.yml",
        resume=True,
        checkpoint_dir=tmp_path,
    )
    assert completed == (
        "crawler",
        "neo4j_upload",
        "metadata_extractor",
        "tentris_upload",
    )


def test_pipeline_retry_on_transient_failure(tmp_path: Path) -> None:
    call_count = 0

    def flaky(_ctx: dict[str, object]) -> None:
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise RuntimeError("transient")

    data = {
        "quest": {
            "name": "retry-test",
            "retry": {"max_attempts": 3, "backoff_seconds": 0},
            "steps": {
                "crawler": {"enabled": False},
                "neo4j_upload": {"enabled": False},
                "metadata_extractor": {"enabled": False},
            },
        }
    }
    config_file = tmp_path / "quest.yml"
    config_file.write_text(yaml.dump(data), encoding="utf-8")

    with patch(
        "open_pulse.pipeline.runner.STEP_REGISTRY",
        {"crawler": flaky, "neo4j_upload": flaky, "metadata_extractor": flaky, "tentris_upload": flaky},
    ):
        completed = run_pipeline(config_file, checkpoint_dir=tmp_path)

    assert "tentris_upload" in completed
    assert call_count == 2


def test_single_step_runs_successfully(tmp_path: Path) -> None:
    run_single_step(tmp_path / "quest.yml", "crawler")


def test_single_step_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown step"):
        run_single_step(Path("quest.yml"), "nonexistent")


# -- Quest CLI command tests -------------------------------------------------


def test_quest_start_runs_pipeline() -> None:
    with patch(
        "open_pulse.commands.quest.run_pipeline",
        return_value=("crawler", "neo4j_upload", "metadata_extractor", "tentris_upload"),
    ):
        result = runner.invoke(app, ["quest", "start"])
    assert result.exit_code == 0
    assert "Pipeline finished" in result.output
    assert "4 step(s)" in result.output


def test_quest_run_step_executes() -> None:
    with patch("open_pulse.commands.quest.run_single_step"):
        result = runner.invoke(app, ["quest", "run-step", "crawler"])
    assert result.exit_code == 0
    assert "completed successfully" in result.output


def test_quest_run_step_unknown_errors() -> None:
    with patch(
        "open_pulse.commands.quest.run_single_step",
        side_effect=ValueError("Unknown step: 'foo'"),
    ):
        result = runner.invoke(app, ["quest", "run-step", "foo"])
    assert result.exit_code == 1
    assert "Unknown step" in result.output


def test_quest_list_steps() -> None:
    result = runner.invoke(app, ["quest", "list-steps"])
    assert result.exit_code == 0
    for name in STEP_NAMES:
        assert name in result.output


# -- Grimoire / health stubs -------------------------------------------------


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
