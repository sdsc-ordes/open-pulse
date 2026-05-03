"""Tests for ``open_pulse.pipeline.neo4j_upload`` and ``Neo4jService.upload``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from open_pulse.pipeline.neo4j_upload import run_neo4j_upload
from open_pulse.services.container import ServiceContainer
from open_pulse.services.crawler import CrawlerService
from open_pulse.services.neo4j import Neo4jService
from open_pulse.services.sparql_store import SparqlStoreService


def _make_container(neo4j: Neo4jService) -> ServiceContainer:
    from open_pulse.services.metadata_extractor import MetadataExtractorService

    return ServiceContainer(
        neo4j=neo4j,
        sparql_store=SparqlStoreService(endpoint="http://localhost:7878"),
        crawler=CrawlerService(
            endpoint="http://localhost:8000",
            api_token_env="CRAWLER_API_TOKEN",
        ),
        metadata_extractor=MetadataExtractorService(endpoint="http://localhost:1234"),
    )


def _sample_graph() -> dict[str, Any]:
    return {
        "users": {
            "alice": {
                "login": "alice",
                "name": "Alice",
                "id": 1,
                "type": "User",
                "is_explored": True,
                "exploration_timestamp": "2026-05-03T00:00:00",
                "authored_repositories": ["alice/repo-a"],
                "forked_repositories": [],
            }
        },
        "orgs": {
            "acme": {
                "login": "acme",
                "name": "ACME",
                "id": 2,
                "type": "Organization",
                "is_explored": True,
                "members": ["alice"],
                "authored_repositories": ["acme/lib"],
                "forked_repositories": [],
            }
        },
        "repos": {
            "alice/repo-a": {
                "full_name": "alice/repo-a",
                "name": "repo-a",
                "id": 100,
                "type": "Repository",
                "owner": "alice",
                "contributors": ["alice"],
            },
            "acme/lib": {
                "full_name": "acme/lib",
                "name": "lib",
                "id": 101,
                "type": "Repository",
                "owner": "acme",
                "contributors": ["alice"],
                "parent_full_name": "upstream/lib",
            },
            "upstream/lib": {
                "full_name": "upstream/lib",
                "name": "lib",
                "id": 99,
                "type": "Repository",
                "owner": "upstream",
                "contributors": [],
            },
        },
    }


# -- Neo4jService.upload (driver mocked) --------------------------------------


def test_upload_invokes_eight_writes_with_correct_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One execute_write per node-type and edge-type — 8 total."""
    monkeypatch.setenv("NEO4J_AUTH", "neo4j/secret")

    fake_session = MagicMock()
    fake_session.__enter__.return_value = fake_session
    fake_session.__exit__.return_value = False
    fake_driver = MagicMock()
    fake_driver.session.return_value = fake_session

    svc = Neo4jService(endpoint="bolt://stub:7687")
    svc._driver = fake_driver  # bypass real driver instantiation

    counts = svc.upload(_sample_graph())

    assert fake_session.execute_write.call_count == 8
    assert counts["users"] == 1
    assert counts["orgs"] == 1
    assert counts["repos"] == 3
    assert counts["owner_edges"] == 2  # alice→alice/repo-a, acme→acme/lib
    assert counts["member_edges"] == 1  # alice MEMBER_OF acme
    # 2 contributors edges total (alice→alice/repo-a, alice→acme/lib)
    assert counts["contributor_edges"] == 2
    assert counts["fork_edges"] == 1  # acme/lib FORK_OF upstream/lib
    assert counts["dependency_edges"] == 0  # _sample_graph has none


def test_upload_handles_empty_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEO4J_AUTH", "neo4j/secret")

    fake_session = MagicMock()
    fake_session.__enter__.return_value = fake_session
    fake_session.__exit__.return_value = False
    fake_driver = MagicMock()
    fake_driver.session.return_value = fake_session

    svc = Neo4jService(endpoint="bolt://stub:7687")
    svc._driver = fake_driver

    counts = svc.upload({"users": {}, "orgs": {}, "repos": {}})

    # Still 8 calls — the merge fns short-circuit internally when rows=[]
    # but the session.execute_write call itself happens.
    assert fake_session.execute_write.call_count == 8
    assert counts == {
        "users": 0,
        "orgs": 0,
        "repos": 0,
        "owner_edges": 0,
        "member_edges": 0,
        "contributor_edges": 0,
        "fork_edges": 0,
        "dependency_edges": 0,
    }


def test_upload_emits_dependency_edges_in_both_directions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`dependencies` fires forward edges, `dependents` fires reverse-direction
    edges of the same DEPENDS_ON type — same edge, different angle."""
    monkeypatch.setenv("NEO4J_AUTH", "neo4j/secret")

    fake_session = MagicMock()
    fake_session.__enter__.return_value = fake_session
    fake_session.__exit__.return_value = False
    fake_driver = MagicMock()
    fake_driver.session.return_value = fake_session

    svc = Neo4jService(endpoint="bolt://stub:7687")
    svc._driver = fake_driver

    graph = {
        "users": {},
        "orgs": {},
        "repos": {
            "owner/lib": {
                "full_name": "owner/lib",
                "dependencies": ["other/utils"],
                "dependents": ["downstream/app"],
            },
        },
    }
    counts = svc.upload(graph)

    assert counts["dependency_edges"] == 2

    # Inspect the rows passed into _merge_dependency_edges (last call).
    last_call = fake_session.execute_write.call_args_list[-1]
    rows = last_call.args[1]  # second positional arg after the tx fn
    pairs = sorted((r["consumer"], r["package"]) for r in rows)
    assert pairs == [
        ("downstream/app", "owner/lib"),  # from dependents[]
        ("owner/lib", "other/utils"),  # from dependencies[]
    ]


def test_upload_raises_when_auth_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEO4J_AUTH", raising=False)
    svc = Neo4jService(endpoint="bolt://stub:7687")
    with pytest.raises(RuntimeError, match="NEO4J_AUTH"):
        svc.upload({"users": {}, "orgs": {}, "repos": {}})


def test_upload_raises_when_auth_malformed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEO4J_AUTH", "no-slash-here")
    svc = Neo4jService(endpoint="bolt://stub:7687")
    with pytest.raises(RuntimeError, match="malformed"):
        svc.upload({"users": {}, "orgs": {}, "repos": {}})


# -- pipeline.neo4j_upload step ----------------------------------------------


def test_run_neo4j_upload_reads_file_and_calls_service(tmp_path: Path) -> None:
    graph = _sample_graph()
    crawler_dir = tmp_path / "crawler-json"
    crawler_dir.mkdir()
    (crawler_dir / "crawler-graph.json").write_text(
        json.dumps(graph), encoding="utf-8"
    )

    neo4j = MagicMock(spec=Neo4jService)
    neo4j.upload.return_value = {
        "users": 1,
        "orgs": 1,
        "repos": 3,
        "owner_edges": 2,
        "member_edges": 1,
        "contributor_edges": 2,
        "fork_edges": 1,
    }
    services = _make_container(neo4j)
    ctx = {
        "services": services,
        "step_config": {
            "input_dir": str(crawler_dir),
            "input_filename": "crawler-graph.json",
        },
    }

    run_neo4j_upload(ctx)

    neo4j.upload.assert_called_once_with(graph)


def test_run_neo4j_upload_raises_when_file_missing(tmp_path: Path) -> None:
    neo4j = MagicMock(spec=Neo4jService)
    services = _make_container(neo4j)
    ctx = {
        "services": services,
        "step_config": {"input_dir": str(tmp_path / "missing")},
    }

    with pytest.raises(FileNotFoundError, match="crawler graph"):
        run_neo4j_upload(ctx)

    neo4j.upload.assert_not_called()


def test_run_neo4j_upload_requires_services_context() -> None:
    with pytest.raises(RuntimeError, match="ServiceContainer"):
        run_neo4j_upload({"step_config": {}})
