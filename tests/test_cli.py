"""Tests for the CLI entry point (help, version)."""

from __future__ import annotations

from typer.testing import CliRunner

from open_pulse.cli import app

runner = CliRunner()


def test_cli_help_exits_cleanly() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_deploy_subcommand_appears_in_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert "deploy" in result.output


def test_quest_subcommand_appears_in_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert "quest" in result.output


def test_services_subcommand_appears_in_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert "services" in result.output


def test_gui_subcommand_appears_in_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert "gui" in result.output


def test_health_subcommand_appears_in_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert "health" in result.output
