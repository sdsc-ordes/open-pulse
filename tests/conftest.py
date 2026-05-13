"""Shared test fixtures for the open-pulse test suite."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from open_pulse.cli import app


@pytest.fixture()
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def invoke(cli_runner: CliRunner):
    """Shortcut: returns a callable that invokes the CLI app."""

    def _invoke(*args: str):
        return cli_runner.invoke(app, list(args))

    return _invoke
