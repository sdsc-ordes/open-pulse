"""Tests for the ``deploy`` command group."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from open_pulse.cli import app
from open_pulse.commands import deploy as deploy_mod

runner = CliRunner()


def test_deploy_up_no_docker_exits_1() -> None:
    with patch.object(deploy_mod, "_docker_available", return_value=False):
        result = runner.invoke(app, ["deploy", "up", "--profile", "default"])
    assert result.exit_code == 1
    assert "Docker" in result.output


def test_deploy_up_with_profile_flag(tmp_path: Path) -> None:
    compose_file = tmp_path / "infra" / "compose" / "docker-compose.yml"
    compose_file.parent.mkdir(parents=True, exist_ok=True)
    compose_file.write_text("services: {}", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("FOO=bar", encoding="utf-8")

    with (
        patch.object(deploy_mod, "_docker_available", return_value=True),
        patch.object(deploy_mod, "_find_project_root", return_value=tmp_path),
        patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
    ):
        result = runner.invoke(app, ["deploy", "up", "--profile", "sparql"])

    assert result.exit_code == 0
    cmd = mock_run.call_args[0][0]
    assert "--profile" in cmd
    assert "sparql" in cmd
    assert "up" in cmd
    assert "-d" in cmd


def test_deploy_up_creates_env_from_template(tmp_path: Path) -> None:
    compose_file = tmp_path / "infra" / "compose" / "docker-compose.yml"
    compose_file.parent.mkdir(parents=True, exist_ok=True)
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


def test_deploy_up_with_compose_override_files(tmp_path: Path) -> None:
    compose_file = tmp_path / "infra" / "compose" / "docker-compose.yml"
    compose_file.parent.mkdir(parents=True, exist_ok=True)
    compose_file.write_text("services: {}", encoding="utf-8")
    override = tmp_path / "docker-compose.override.yml"
    override.write_text("services: {}", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("FOO=bar", encoding="utf-8")

    with (
        patch.object(deploy_mod, "_docker_available", return_value=True),
        patch.object(deploy_mod, "_find_project_root", return_value=tmp_path),
        patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
    ):
        result = runner.invoke(
            app,
            ["deploy", "up", "--profile", "default", "--file", str(override)],
        )

    assert result.exit_code == 0
    cmd = mock_run.call_args[0][0]
    assert "-f" in cmd
    assert str(override) in cmd


def test_deploy_up_with_cli_includes_cli_compose_file(tmp_path: Path) -> None:
    compose_file = tmp_path / "infra" / "compose" / "docker-compose.yml"
    compose_file.parent.mkdir(parents=True, exist_ok=True)
    compose_file.write_text("services: {}", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("FOO=bar", encoding="utf-8")

    with (
        patch.object(deploy_mod, "_docker_available", return_value=True),
        patch.object(deploy_mod, "_find_project_root", return_value=tmp_path),
        patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
    ):
        result = runner.invoke(
            app,
            ["deploy", "up", "--profile", "default", "--with-cli"],
        )

    assert result.exit_code == 0
    cmd = mock_run.call_args[0][0]
    assert "-f" in cmd
    assert str(tmp_path / "infra" / "compose" / "docker-compose.cli.yml") in cmd


def test_deploy_down_runs_compose_down(tmp_path: Path) -> None:
    compose_file = tmp_path / "infra" / "compose" / "docker-compose.yml"
    compose_file.parent.mkdir(parents=True, exist_ok=True)
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


def test_deploy_down_with_volumes_flag(tmp_path: Path) -> None:
    compose_file = tmp_path / "infra" / "compose" / "docker-compose.yml"
    compose_file.parent.mkdir(parents=True, exist_ok=True)
    compose_file.write_text("services: {}", encoding="utf-8")

    with (
        patch.object(deploy_mod, "_docker_available", return_value=True),
        patch.object(deploy_mod, "_find_project_root", return_value=tmp_path),
        patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
    ):
        result = runner.invoke(app, ["deploy", "down", "--volumes"])

    assert result.exit_code == 0
    cmd = mock_run.call_args[0][0]
    assert "down" in cmd
    assert "--volumes" in cmd


def test_deploy_down_with_cli_includes_cli_compose_file(tmp_path: Path) -> None:
    compose_file = tmp_path / "infra" / "compose" / "docker-compose.yml"
    compose_file.parent.mkdir(parents=True, exist_ok=True)
    compose_file.write_text("services: {}", encoding="utf-8")

    with (
        patch.object(deploy_mod, "_docker_available", return_value=True),
        patch.object(deploy_mod, "_find_project_root", return_value=tmp_path),
        patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
    ):
        result = runner.invoke(app, ["deploy", "down", "--with-cli"])

    assert result.exit_code == 0
    cmd = mock_run.call_args[0][0]
    assert str(tmp_path / "infra" / "compose" / "docker-compose.cli.yml") in cmd


def test_deploy_down_no_docker_exits_1() -> None:
    with patch.object(deploy_mod, "_docker_available", return_value=False):
        result = runner.invoke(app, ["deploy", "down"])
    assert result.exit_code == 1
    assert "Docker" in result.output


def test_deploy_ps_runs_compose_ps(tmp_path: Path) -> None:
    compose_file = tmp_path / "infra" / "compose" / "docker-compose.yml"
    compose_file.parent.mkdir(parents=True, exist_ok=True)
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


def test_deploy_ps_with_cli_includes_cli_compose_file(tmp_path: Path) -> None:
    compose_file = tmp_path / "infra" / "compose" / "docker-compose.yml"
    compose_file.parent.mkdir(parents=True, exist_ok=True)
    compose_file.write_text("services: {}", encoding="utf-8")

    with (
        patch.object(deploy_mod, "_docker_available", return_value=True),
        patch.object(deploy_mod, "_find_project_root", return_value=tmp_path),
        patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
    ):
        result = runner.invoke(app, ["deploy", "ps", "--with-cli"])

    assert result.exit_code == 0
    cmd = mock_run.call_args[0][0]
    assert str(tmp_path / "infra" / "compose" / "docker-compose.cli.yml") in cmd


def test_deploy_ps_no_docker_exits_1() -> None:
    with patch.object(deploy_mod, "_docker_available", return_value=False):
        result = runner.invoke(app, ["deploy", "ps"])
    assert result.exit_code == 1
    assert "Docker" in result.output
