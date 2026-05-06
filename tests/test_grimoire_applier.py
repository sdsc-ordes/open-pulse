"""Tests for the SPARQL → projects.json → applier glue."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from typer.testing import CliRunner

from open_pulse.cli import app
from open_pulse.utils.grimoire.applier_client import (
    DEFAULT_QUERY,
    build_projects_json,
    post_to_applier,
    query_sparql_for_repos,
)

runner = CliRunner()


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)


# -- query_sparql_for_repos --------------------------------------------------


def test_query_returns_unique_sorted_repos() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/query"
        assert request.url.params.get("query") == DEFAULT_QUERY
        return httpx.Response(
            200,
            json={
                "results": {
                    "bindings": [
                        {"repo": {"value": "https://github.com/b/repo"}},
                        {"repo": {"value": "https://github.com/a/repo"}},
                        {"repo": {"value": "https://github.com/a/repo"}},  # dup
                        {"repo": {"value": "not-a-url"}},                    # dropped
                    ]
                }
            },
        )

    with _mock_client(handler) as c:
        repos = query_sparql_for_repos("http://sparql:7878", client=c)
    assert repos == ["https://github.com/a/repo", "https://github.com/b/repo"]


def test_query_appends_query_path_when_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("http://sparql:7878/query?")
        return httpx.Response(200, json={"results": {"bindings": []}})

    with _mock_client(handler) as c:
        query_sparql_for_repos("http://sparql:7878", client=c)


def test_query_keeps_query_path_when_supplied() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/query"  # no double /query/query
        return httpx.Response(200, json={"results": {"bindings": []}})

    with _mock_client(handler) as c:
        query_sparql_for_repos("http://sparql:7878/query", client=c)


def test_query_uses_basic_auth_when_provided() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization", "").startswith("Basic ")
        return httpx.Response(200, json={"results": {"bindings": []}})

    with _mock_client(handler) as c:
        query_sparql_for_repos(
            "http://sparql:7878", auth=("alice", "secret"), client=c
        )


def test_query_raises_on_non_200() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad creds")

    with _mock_client(handler) as c:
        with pytest.raises(RuntimeError, match="HTTP 401"):
            query_sparql_for_repos("http://sparql:7878", client=c)


# -- build_projects_json -----------------------------------------------------


def test_build_projects_json_uses_slugified_title() -> None:
    out = build_projects_json(
        ["https://github.com/x/y"], group_title="Open Pulse SPARQL"
    )
    assert list(out.keys()) == ["open_pulse_sparql"]
    assert out["open_pulse_sparql"]["meta"]["title"] == "Open Pulse SPARQL"
    assert out["open_pulse_sparql"]["git"] == ["https://github.com/x/y"]


def test_build_projects_json_falls_back_when_title_is_empty() -> None:
    out = build_projects_json([], group_title="!!!")
    assert list(out.keys()) == ["open_pulse_sparql"]


# -- post_to_applier ---------------------------------------------------------


def test_post_to_applier_sends_bearer_and_returns_body() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"status": "applied", "detail": "restarted mordred"}
        )

    with _mock_client(handler) as c:
        body = post_to_applier(
            "http://applier:1235", "TOK", {"my_group": {"git": []}}, client=c
        )
    assert body["status"] == "applied"
    assert captured["auth"] == "Bearer TOK"
    assert captured["url"] == "http://applier:1235/apply"


def test_post_to_applier_raises_on_non_200() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad token")

    with _mock_client(handler) as c:
        with pytest.raises(RuntimeError, match="HTTP 401"):
            post_to_applier("http://applier:1235", "WRONG", {"x": {}}, client=c)


# -- CLI: open-pulse services grimoire apply ---------------------------------


def test_cli_apply_dry_run_skips_post(tmp_path: Path) -> None:
    out_path = tmp_path / "projects.json"
    repos = ["https://github.com/x/y", "https://github.com/a/b"]

    with (
        patch(
            "open_pulse.utils.grimoire.applier_client.query_sparql_for_repos",
            return_value=sorted(repos),
        ),
        patch(
            "open_pulse.utils.grimoire.applier_client.post_to_applier",
        ) as mock_post,
    ):
        result = runner.invoke(
            app,
            [
                "services", "grimoire", "apply",
                "--sparql", "http://sparql:7878",
                "--dry-run",
                "--output", str(out_path),
            ],
        )

    assert result.exit_code == 0, result.output
    mock_post.assert_not_called()
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert "open_pulse_sparql" in written
    assert sorted(written["open_pulse_sparql"]["git"]) == sorted(repos)


def test_cli_apply_posts_to_applier_when_token_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPLIER_AUTH", "abc123")
    repos = ["https://github.com/x/y"]

    with (
        patch(
            "open_pulse.utils.grimoire.applier_client.query_sparql_for_repos",
            return_value=repos,
        ),
        patch(
            "open_pulse.utils.grimoire.applier_client.post_to_applier",
            return_value={"status": "applied", "detail": "ok"},
        ) as mock_post,
    ):
        result = runner.invoke(
            app,
            [
                "services", "grimoire", "apply",
                "--applier", "http://applier:1235",
            ],
        )

    assert result.exit_code == 0, result.output
    mock_post.assert_called_once()
    args = mock_post.call_args.args
    assert args[0] == "http://applier:1235"
    assert args[1] == "abc123"


def test_cli_apply_fails_when_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APPLIER_AUTH", raising=False)
    repos = ["https://github.com/x/y"]

    with patch(
        "open_pulse.utils.grimoire.applier_client.query_sparql_for_repos",
        return_value=repos,
    ):
        result = runner.invoke(
            app,
            ["services", "grimoire", "apply"],
        )

    assert result.exit_code == 1
    assert "APPLIER_AUTH" in result.output


def test_cli_apply_uses_query_file(tmp_path: Path) -> None:
    qfile = tmp_path / "q.rq"
    qfile.write_text("ASK { }", encoding="utf-8")

    with (
        patch(
            "open_pulse.utils.grimoire.applier_client.query_sparql_for_repos",
            return_value=[],
        ) as mock_query,
        patch(
            "open_pulse.utils.grimoire.applier_client.post_to_applier",
        ),
    ):
        result = runner.invoke(
            app,
            [
                "services", "grimoire", "apply",
                "--query-file", str(qfile),
                "--dry-run",
            ],
        )

    assert result.exit_code == 0, result.output
    _, kwargs = mock_query.call_args
    assert kwargs["query"] == "ASK { }"


def test_cli_apply_rejects_query_and_query_file_together(tmp_path: Path) -> None:
    qfile = tmp_path / "q.rq"
    qfile.write_text("ASK { }", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "services", "grimoire", "apply",
            "--query", "ASK { }",
            "--query-file", str(qfile),
        ],
    )
    assert result.exit_code == 2
