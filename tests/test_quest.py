"""Tests for the ``quest`` command group, pipeline config, and runner."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from open_pulse.cli import app
from open_pulse.pipeline.config import QuestFileConfig
from open_pulse.pipeline.runner import (
    STEP_NAMES,
    build_tasks,
    load_config,
    run_pipeline,
    run_single_step,
)

runner = CliRunner()


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


def test_quest_start_with_resume_flag() -> None:
    with patch(
        "open_pulse.commands.quest.run_pipeline",
        return_value=("crawler", "neo4j_upload", "metadata_extractor", "tentris_upload"),
    ) as mock_run:
        result = runner.invoke(app, ["quest", "start", "--resume"])

    assert result.exit_code == 0
    mock_run.assert_called_once()
    _, kwargs = mock_run.call_args
    assert kwargs.get("resume") is True


def test_quest_start_with_custom_config(tmp_path: Path) -> None:
    cfg = tmp_path / "custom.yml"
    cfg.write_text("quest:\n  name: custom\n", encoding="utf-8")

    with patch(
        "open_pulse.commands.quest.run_pipeline",
        return_value=("crawler",),
    ) as mock_run:
        result = runner.invoke(app, ["quest", "start", "--config", str(cfg)])

    assert result.exit_code == 0
    call_args = mock_run.call_args
    assert str(call_args[0][0]) == str(cfg)


def test_quest_start_pipeline_failure_propagates() -> None:
    with patch(
        "open_pulse.commands.quest.run_pipeline",
        side_effect=RuntimeError("pipeline exploded"),
    ):
        result = runner.invoke(app, ["quest", "start"])
    assert result.exit_code != 0


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
