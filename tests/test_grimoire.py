"""Tests for the ``grimoire`` command group and supporting modules."""

from __future__ import annotations

import builtins
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from open_pulse.cli import app

runner = CliRunner()

_original_import = builtins.__import__


# -- Grimoire CLI command tests ----------------------------------------------


def test_grimoire_prepare_config_writes_json(tmp_path: Path) -> None:
    output = tmp_path / "projects.json"
    result = runner.invoke(
        app,
        [
            "grimoire",
            "prepare-config",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0
    assert output.is_file()
    data = json.loads(output.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


def test_grimoire_prepare_config_custom_endpoints(tmp_path: Path) -> None:
    output = tmp_path / "out.json"
    result = runner.invoke(
        app,
        [
            "grimoire",
            "prepare-config",
            "--neo4j",
            "bolt://db:7687",
            "--tentris",
            "http://sparql:9000/sparql",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0
    assert "bolt://db:7687" in result.output
    assert "http://sparql:9000/sparql" in result.output


def _import_without_streamlit(name: str, *args: object, **kwargs: object) -> object:
    """Helper to simulate missing streamlit."""
    if name == "streamlit":
        raise ImportError("No module named 'streamlit'")
    return _original_import(name, *args, **kwargs)


def test_grimoire_ui_missing_streamlit_exits_1() -> None:
    with patch.dict("sys.modules", {"streamlit": None}):
        with patch("builtins.__import__", side_effect=_import_without_streamlit):
            result = runner.invoke(app, ["grimoire", "ui"])
    assert result.exit_code == 1
    assert "Streamlit" in result.output


def test_grimoire_ui_launches_streamlit() -> None:
    mock_streamlit = MagicMock()
    with (
        patch.dict("sys.modules", {"streamlit": mock_streamlit}),
        patch(
            "open_pulse.grimoire.streamlit_app.launch_streamlit",
        ) as mock_launch,
    ):
        result = runner.invoke(app, ["grimoire", "ui"])
    assert result.exit_code == 0
    mock_launch.assert_called_once()


def test_grimoire_install_watcher_requires_repo() -> None:
    result = runner.invoke(app, ["grimoire", "install-watcher"])
    assert result.exit_code != 0


def test_grimoire_install_watcher_calls_installer() -> None:
    with patch(
        "open_pulse.grimoire.cronjob.install_watcher",
    ) as mock_install:
        result = runner.invoke(
            app,
            [
                "grimoire",
                "install-watcher",
                "--repo",
                "https://github.com/org/repo.git",
                "--branch",
                "develop",
                "--schedule",
                "0 * * * *",
            ],
        )
    assert result.exit_code == 0
    mock_install.assert_called_once_with(
        repo_url="https://github.com/org/repo.git",
        config_path="projects.json",
        branch="develop",
        schedule="0 * * * *",
        clone_dir=None,
    )


def test_grimoire_install_watcher_with_clone_dir(tmp_path: Path) -> None:
    with patch("open_pulse.grimoire.cronjob.install_watcher") as mock_install:
        result = runner.invoke(
            app,
            [
                "grimoire",
                "install-watcher",
                "--repo",
                "https://github.com/org/repo.git",
                "--clone-dir",
                str(tmp_path / "watcher"),
            ],
        )
    assert result.exit_code == 0
    mock_install.assert_called_once()
    assert mock_install.call_args.kwargs["clone_dir"] == tmp_path / "watcher"


# -- Grimoire unit tests (sparql_config) -------------------------------------


def test_sparql_generate_config_empty_repos(tmp_path: Path) -> None:
    from open_pulse.grimoire.sparql_config import generate_config

    out = generate_config(output=tmp_path / "projects.json")
    assert out.is_file()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data == {}


def test_sparql_build_projects_json() -> None:
    from open_pulse.grimoire.sparql_config import _build_projects_json

    repos = [
        {"name": "alpha", "repo": "https://github.com/org/alpha"},
        {"name": "beta", "repo": "https://github.com/org/beta"},
    ]
    result = _build_projects_json(repos)
    assert result == {
        "alpha": {"git": ["https://github.com/org/alpha"]},
        "beta": {"git": ["https://github.com/org/beta"]},
    }


# -- Grimoire unit tests (cronjob) ------------------------------------------


def test_cronjob_build_watcher_script() -> None:
    from open_pulse.grimoire.cronjob import _build_watcher_script

    script = _build_watcher_script(
        repo_url="https://github.com/org/repo.git",
        config_path="config.json",
        branch="main",
        clone_dir=Path("/tmp/watcher"),
    )
    assert "git clone" in script
    assert "config.json" in script
    assert "/tmp/watcher" in script


def test_cronjob_windows_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    from open_pulse.grimoire.cronjob import install_watcher

    monkeypatch.setattr("open_pulse.grimoire.cronjob.platform.system", lambda: "Windows")
    with pytest.raises(SystemExit):
        install_watcher(repo_url="https://github.com/org/repo.git")
