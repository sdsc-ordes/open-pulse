"""Tests for the ``health`` command and service health probes."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from open_pulse.cli import app
from open_pulse.commands import health as health_mod
from open_pulse.services import health as svc_health
from open_pulse.services.config import (
    DEFAULT_CRAWLER_ENDPOINT,
    DEFAULT_NEO4J_BOLT_ENDPOINT,
    DEFAULT_NEO4J_HTTP_ENDPOINT,
    DEFAULT_SPARQL_ENDPOINT,
)

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
        ("SPARQL store", "http://localhost:7878", True, "HTTP 200"),
        ("GrimoireLab DB", "localhost:5432", True, "connection established"),
        ("Crawler API", "http://localhost:8000/api/v1/health", True, "HTTP 200"),
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
        ("SPARQL store", "http://localhost:7878", True, "HTTP 200"),
        ("GrimoireLab DB", "localhost:5432", True, "connection established"),
        ("Crawler API", "http://localhost:8000/api/v1/health", True, "HTTP 200"),
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
                "--sparql",
                "http://sparql:9000/sparql",
                "--grimoirelab-db",
                "pghost:5433",
                "--crawler",
                "http://crawler-host:8000",
            ],
        )

    mock_probe.assert_called_once_with(
        "http://db:7474",
        "bolt://db:7687",
        "http://sparql:9000/sparql",
        "pghost:5433",
        "http://crawler-host:8000",
    )


def test_health_default_endpoints_come_from_service_config() -> None:
    with (
        patch.object(health_mod, "_docker_available", return_value=False),
        patch.object(health_mod, "_probe_endpoints") as mock_probe,
        patch.object(health_mod, "_smoke_tests", return_value=[]),
    ):
        mock_probe.return_value = []
        runner.invoke(app, ["health"])

    mock_probe.assert_called_once_with(
        DEFAULT_NEO4J_HTTP_ENDPOINT,
        DEFAULT_NEO4J_BOLT_ENDPOINT,
        DEFAULT_SPARQL_ENDPOINT,
        "localhost:5432",
        DEFAULT_CRAWLER_ENDPOINT,
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
            "Name": "sparql-store-open-pulse",
            "Service": "sparql_store",
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


# -- Service health unit tests -----------------------------------------------


def test_probe_http_success() -> None:
    with patch("open_pulse.services.health.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda self: self
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        ok, detail = svc_health.probe_http("http://localhost:7474")

    assert ok is True
    assert "200" in detail


def test_probe_http_unreachable() -> None:
    from urllib.error import URLError

    with patch(
        "open_pulse.services.health.urlopen",
        side_effect=URLError("Connection refused"),
    ):
        ok, detail = svc_health.probe_http("http://localhost:9999")

    assert ok is False
    assert "refused" in detail.lower()


def test_probe_tcp_success() -> None:
    mock_sock = MagicMock()
    mock_sock.__enter__ = lambda self: self
    mock_sock.__exit__ = MagicMock(return_value=False)

    with patch("open_pulse.services.health.socket.create_connection", return_value=mock_sock):
        ok, detail = svc_health.probe_tcp("localhost", 7687)

    assert ok is True
    assert "established" in detail


def test_probe_tcp_refused() -> None:
    with patch(
        "open_pulse.services.health.socket.create_connection",
        side_effect=OSError("Connection refused"),
    ):
        ok, detail = svc_health.probe_tcp("localhost", 9999)

    assert ok is False
    assert "refused" in detail.lower()


def test_parse_host_port() -> None:
    assert svc_health.parse_host_port("myhost:1234", 5432) == ("myhost", 1234)
    assert svc_health.parse_host_port("myhost", 5432) == ("myhost", 5432)
    assert svc_health.parse_host_port("myhost:bad", 5432) == ("myhost:bad", 5432)


def test_probe_endpoints_uses_service_clients() -> None:
    with (
        patch("open_pulse.services.health.probe_http", return_value=(True, "HTTP 200")),
        patch("open_pulse.services.health.probe_tcp", return_value=(True, "connection established")),
        patch("open_pulse.services.health.Neo4jService.check_bolt", return_value=(True, "connection established")),
        patch("open_pulse.services.health.SparqlStoreService.check_sparql", return_value=(True, "HTTP 200")),
        patch("open_pulse.services.health.CrawlerService.check_health", return_value=(True, "HTTP 200")),
    ):
        result = svc_health.probe_endpoints(
            "http://localhost:7474",
            "bolt://localhost:7687",
            "http://localhost:7878",
            "localhost:5432",
            "http://localhost:8000",
        )

    assert len(result) == 5
    assert result[0][0] == "Neo4j (HTTP)"
    assert result[1][0] == "Neo4j (Bolt)"
    assert result[2][0] == "SPARQL store"
    assert result[3][0] == "GrimoireLab DB"
    assert result[4][0] == "Crawler API"
    assert result[4][1] == "http://localhost:8000/api/v1/health"


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
