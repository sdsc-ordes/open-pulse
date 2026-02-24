"""Tests for the ``health`` command and supporting probe functions."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from open_pulse.cli import app
from open_pulse.commands import health as health_mod

runner = CliRunner()


# -- Health CLI command tests ------------------------------------------------


def test_health_no_docker_reports_unreachable() -> None:
    with patch.object(health_mod, "_docker_available", return_value=False):
        with patch.object(health_mod, "_probe_endpoints", return_value=[]):
            with patch.object(
                health_mod,
                "_smoke_tests",
                return_value=[("CLI version", True, "v0.1.0")],
            ):
                result = runner.invoke(app, ["health"])

    assert result.exit_code == 1
    assert "not" in result.output and "reachable" in result.output


def test_health_all_ok(tmp_path: Path) -> None:
    containers = [
        {
            "Name": "neo4j-open-pulse",
            "Service": "neo4j",
            "State": "running",
            "Status": "Up 5 minutes (healthy)",
            "Ports": "7474/tcp, 7687/tcp",
        }
    ]
    endpoints = [
        ("Neo4j (HTTP)", "http://localhost:7474", True, "HTTP 200"),
        ("Neo4j (Bolt)", "bolt://localhost:7687", True, "connection established"),
        ("Tentris (SPARQL)", "http://localhost:7502/sparql", True, "HTTP 200"),
        ("GrimoireLab DB", "localhost:5432", True, "connection established"),
    ]
    smoke = [
        ("CLI version", True, "v0.1.0"),
        ("Pipeline config schema", True, "default config validates"),
    ]

    with (
        patch.object(health_mod, "_docker_available", return_value=True),
        patch.object(health_mod, "_find_project_root", return_value=tmp_path),
        patch.object(health_mod, "_get_container_statuses", return_value=containers),
        patch.object(health_mod, "_probe_endpoints", return_value=endpoints),
        patch.object(health_mod, "_smoke_tests", return_value=smoke),
    ):
        result = runner.invoke(app, ["health"])

    assert result.exit_code == 0
    assert "All checks passed" in result.output


def test_health_failing_endpoint_exits_1(tmp_path: Path) -> None:
    endpoints = [
        ("Neo4j (HTTP)", "http://localhost:7474", False, "Connection refused"),
        ("Neo4j (Bolt)", "bolt://localhost:7687", True, "connection established"),
        ("Tentris (SPARQL)", "http://localhost:7502/sparql", True, "HTTP 200"),
        ("GrimoireLab DB", "localhost:5432", True, "connection established"),
    ]

    with (
        patch.object(health_mod, "_docker_available", return_value=True),
        patch.object(health_mod, "_find_project_root", return_value=tmp_path),
        patch.object(health_mod, "_get_container_statuses", return_value=[]),
        patch.object(health_mod, "_probe_endpoints", return_value=endpoints),
        patch.object(health_mod, "_smoke_tests", return_value=[]),
    ):
        result = runner.invoke(app, ["health"])

    assert result.exit_code == 1
    assert "Some checks failed" in result.output


def test_health_stopped_container_exits_1(tmp_path: Path) -> None:
    containers = [
        {
            "Name": "neo4j-open-pulse",
            "Service": "neo4j",
            "State": "exited",
            "Status": "Exited (1) 2 minutes ago",
            "Ports": "",
        }
    ]

    with (
        patch.object(health_mod, "_docker_available", return_value=True),
        patch.object(health_mod, "_find_project_root", return_value=tmp_path),
        patch.object(health_mod, "_get_container_statuses", return_value=containers),
        patch.object(health_mod, "_probe_endpoints", return_value=[]),
        patch.object(health_mod, "_smoke_tests", return_value=[]),
    ):
        result = runner.invoke(app, ["health"])

    assert result.exit_code == 1
    assert "exited" in result.output


def test_health_custom_endpoints() -> None:
    with (
        patch.object(health_mod, "_docker_available", return_value=False),
        patch.object(health_mod, "_probe_endpoints") as mock_probe,
        patch.object(health_mod, "_smoke_tests", return_value=[]),
    ):
        mock_probe.return_value = []
        runner.invoke(
            app,
            [
                "health",
                "--neo4j",
                "http://db:7474",
                "--neo4j-bolt",
                "bolt://db:7687",
                "--tentris",
                "http://sparql:9000/sparql",
                "--grimoirelab-db",
                "pghost:5433",
            ],
        )

    mock_probe.assert_called_once_with(
        "http://db:7474",
        "bolt://db:7687",
        "http://sparql:9000/sparql",
        "pghost:5433",
    )


def test_health_no_containers_shows_hint(tmp_path: Path) -> None:
    with (
        patch.object(health_mod, "_docker_available", return_value=True),
        patch.object(health_mod, "_find_project_root", return_value=tmp_path),
        patch.object(health_mod, "_get_container_statuses", return_value=[]),
        patch.object(health_mod, "_probe_endpoints", return_value=[]),
        patch.object(health_mod, "_smoke_tests", return_value=[]),
    ):
        result = runner.invoke(app, ["health"])

    assert "No containers found" in result.output


def test_health_multiple_containers_mixed_states(tmp_path: Path) -> None:
    containers = [
        {
            "Name": "neo4j-open-pulse",
            "Service": "neo4j",
            "State": "running",
            "Status": "Up 5 minutes",
            "Ports": "7474/tcp",
        },
        {
            "Name": "tentris-open-pulse",
            "Service": "tentris",
            "State": "exited",
            "Status": "Exited (1) 1 minute ago",
            "Ports": "",
        },
    ]

    with (
        patch.object(health_mod, "_docker_available", return_value=True),
        patch.object(health_mod, "_find_project_root", return_value=tmp_path),
        patch.object(health_mod, "_get_container_statuses", return_value=containers),
        patch.object(health_mod, "_probe_endpoints", return_value=[]),
        patch.object(health_mod, "_smoke_tests", return_value=[]),
    ):
        result = runner.invoke(app, ["health"])

    assert result.exit_code == 1


# -- Health unit tests -------------------------------------------------------


def test_probe_http_success() -> None:
    with patch("open_pulse.commands.health.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda self: self
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        ok, detail = health_mod._probe_http("http://localhost:7474")

    assert ok is True
    assert "200" in detail


def test_probe_http_unreachable() -> None:
    from urllib.error import URLError

    with patch(
        "open_pulse.commands.health.urlopen",
        side_effect=URLError("Connection refused"),
    ):
        ok, detail = health_mod._probe_http("http://localhost:9999")

    assert ok is False
    assert "refused" in detail.lower()


def test_probe_tcp_success() -> None:
    mock_sock = MagicMock()
    mock_sock.__enter__ = lambda self: self
    mock_sock.__exit__ = MagicMock(return_value=False)

    with patch("open_pulse.commands.health.socket.create_connection", return_value=mock_sock):
        ok, detail = health_mod._probe_tcp("localhost", 7687)

    assert ok is True
    assert "established" in detail


def test_probe_tcp_refused() -> None:
    with patch(
        "open_pulse.commands.health.socket.create_connection",
        side_effect=OSError("Connection refused"),
    ):
        ok, detail = health_mod._probe_tcp("localhost", 9999)

    assert ok is False
    assert "refused" in detail.lower()


def test_parse_host_port() -> None:
    assert health_mod._parse_host_port("myhost:1234", 5432) == ("myhost", 1234)
    assert health_mod._parse_host_port("myhost", 5432) == ("myhost", 5432)
    assert health_mod._parse_host_port("myhost:bad", 5432) == ("myhost:bad", 5432)


def test_smoke_tests_include_version() -> None:
    results = health_mod._smoke_tests(None, docker_ok=False)
    labels = [r[0] for r in results]
    assert "CLI version" in labels
    assert "Pipeline config schema" in labels

    for _label, passed, _detail in results:
        assert passed is True


def test_get_container_statuses_handles_empty(tmp_path: Path) -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        result = health_mod._get_container_statuses(tmp_path)
    assert result == []


def test_get_container_statuses_parses_json(tmp_path: Path) -> None:
    json_line = json.dumps(
        {"Name": "neo4j-open-pulse", "Service": "neo4j", "State": "running"}
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=json_line + "\n")
        result = health_mod._get_container_statuses(tmp_path)

    assert len(result) == 1
    assert result[0]["Name"] == "neo4j-open-pulse"
