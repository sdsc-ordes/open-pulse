"""Tests for ``open_pulse.pipeline.crawler``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from open_pulse.pipeline.crawler import run_crawler
from open_pulse.services.container import ServiceContainer
from open_pulse.services.crawler import (
    CrawlerJobFailedError,
    CrawlerService,
)
from open_pulse.services.neo4j import Neo4jService
from open_pulse.services.sparql_store import SparqlStoreService


def _make_container(crawler: CrawlerService) -> ServiceContainer:
    from open_pulse.services.metadata_extractor import MetadataExtractorService

    return ServiceContainer(
        neo4j=Neo4jService(endpoint="bolt://localhost:7687"),
        sparql_store=SparqlStoreService(endpoint="http://localhost:7878"),
        crawler=crawler,
        metadata_extractor=MetadataExtractorService(endpoint="http://localhost:1234"),
    )


def _step_config(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "seeds": ["sdsc-ordes/open-pulse"],
        "max_rounds": 1,
        "crawl_dependencies": False,
        "crawl_dependents": False,
        "min_stars": 0,
        "max_dependents": None,
        "batch_size": None,
        "output_dir": str(tmp_path / "crawler-json"),
        "output_filename": "crawler-graph.json",
        "poll_interval_seconds": 0.0,
        "timeout_seconds": 5.0,
    }
    cfg.update(overrides)
    return cfg


def test_run_crawler_writes_graph_file(tmp_path: Path) -> None:
    graph_payload = {"users": [{"login": "alice"}], "orgs": [], "repos": []}
    crawler = MagicMock(spec=CrawlerService)
    crawler.submit_crawl.return_value = "job-1"
    crawler.wait_for_completion.return_value = {
        "status": "completed",
        "users": 1,
        "orgs": 0,
        "repos": 0,
    }
    crawler.get_graph.return_value = graph_payload

    services = _make_container(crawler)
    ctx = {"services": services, "step_config": _step_config(tmp_path)}

    run_crawler(ctx)

    out = tmp_path / "crawler-json" / "crawler-graph.json"
    assert out.is_file()
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written == graph_payload

    submitted_body = crawler.submit_crawl.call_args.args[0]
    assert submitted_body["seeds"] == ["sdsc-ordes/open-pulse"]
    assert submitted_body["max_rounds"] == 1
    assert "output_dir" not in submitted_body  # local-IO fields must not leak
    assert "poll_interval_seconds" not in submitted_body
    assert "api_version" not in submitted_body  # routing field, not a body field
    # Defaults to v1 across the whole lifecycle.
    assert crawler.submit_crawl.call_args.kwargs["api_version"] == "v1"
    assert crawler.wait_for_completion.call_args.kwargs["api_version"] == "v1"
    assert crawler.get_graph.call_args.kwargs["api_version"] == "v1"


def test_run_crawler_threads_api_version_v2(tmp_path: Path) -> None:
    crawler = MagicMock(spec=CrawlerService)
    crawler.submit_crawl.return_value = "job-v2"
    crawler.wait_for_completion.return_value = {"status": "completed"}
    crawler.get_graph.return_value = {"users": [], "orgs": [], "repos": []}

    services = _make_container(crawler)
    ctx = {
        "services": services,
        "step_config": _step_config(tmp_path, api_version="v2"),
    }

    run_crawler(ctx)

    # api_version must reach submit + poll + graph fetch so all three hit v2.
    assert crawler.submit_crawl.call_args.kwargs["api_version"] == "v2"
    assert crawler.wait_for_completion.call_args.kwargs["api_version"] == "v2"
    assert crawler.get_graph.call_args.kwargs["api_version"] == "v2"
    # It is a routing choice, not a crawler body field.
    assert "api_version" not in crawler.submit_crawl.call_args.args[0]


def test_run_crawler_raises_on_empty_seeds(tmp_path: Path) -> None:
    crawler = MagicMock(spec=CrawlerService)
    services = _make_container(crawler)
    ctx = {
        "services": services,
        "step_config": _step_config(tmp_path, seeds=[]),
    }

    with pytest.raises(ValueError, match="at least one seed"):
        run_crawler(ctx)

    crawler.submit_crawl.assert_not_called()


def test_run_crawler_propagates_job_failed(tmp_path: Path) -> None:
    crawler = MagicMock(spec=CrawlerService)
    crawler.submit_crawl.return_value = "job-2"
    crawler.wait_for_completion.side_effect = CrawlerJobFailedError("boom")

    services = _make_container(crawler)
    ctx = {"services": services, "step_config": _step_config(tmp_path)}

    with pytest.raises(CrawlerJobFailedError, match="boom"):
        run_crawler(ctx)

    crawler.get_graph.assert_not_called()


def test_run_crawler_requires_services_context() -> None:
    with pytest.raises(RuntimeError, match="ServiceContainer"):
        run_crawler({"step_config": {"seeds": ["x"]}})


def test_run_crawler_atomic_write_no_tmp_left_behind(tmp_path: Path) -> None:
    crawler = MagicMock(spec=CrawlerService)
    crawler.submit_crawl.return_value = "job-3"
    crawler.wait_for_completion.return_value = {"status": "completed"}
    crawler.get_graph.return_value = {"users": [], "orgs": [], "repos": []}

    services = _make_container(crawler)
    ctx = {"services": services, "step_config": _step_config(tmp_path)}
    run_crawler(ctx)

    out_dir = tmp_path / "crawler-json"
    assert (out_dir / "crawler-graph.json").is_file()
    leftover = list(out_dir.glob("*.tmp"))
    assert leftover == []
