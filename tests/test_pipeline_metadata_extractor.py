"""Tests for ``open_pulse.pipeline.metadata_extractor``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from open_pulse.pipeline.metadata_extractor import (
    _safe_filename,
    run_metadata_extractor,
)
from open_pulse.services.container import ServiceContainer
from open_pulse.services.crawler import CrawlerService
from open_pulse.services.metadata_extractor import MetadataExtractorService
from open_pulse.services.neo4j import Neo4jService
from open_pulse.services.sparql_store import SparqlStoreService


def _make_container(extractor: MetadataExtractorService) -> ServiceContainer:
    return ServiceContainer(
        neo4j=Neo4jService(endpoint="bolt://localhost:7687"),
        sparql_store=SparqlStoreService(endpoint="http://localhost:7878"),
        crawler=CrawlerService(
            endpoint="http://localhost:8000",
            api_token_env="CRAWLER_API_TOKEN",
        ),
        metadata_extractor=extractor,
    )


def _seed_crawler_graph(
    crawler_dir: Path, repos: list[str]
) -> Path:
    crawler_dir.mkdir(parents=True, exist_ok=True)
    graph = {
        "users": {},
        "orgs": {},
        "repos": {full: {"full_name": full} for full in repos},
    }
    path = crawler_dir / "crawler-graph.json"
    path.write_text(json.dumps(graph), encoding="utf-8")
    return path


def _gimie_response(repo: str) -> dict[str, Any]:
    return {
        "link": f"https://github.com/{repo}",
        "type": "repository",
        "parsedTimestamp": "2026-05-03T00:00:00",
        "output": [{"@id": f"https://github.com/{repo}", "@type": ["http://schema.org/SoftwareSourceCode"]}],
        "stats": {"duration": 1.0, "status_code": 200},
    }


# -- _safe_filename ----------------------------------------------------------


def test_safe_filename_strips_unsafe_chars() -> None:
    assert _safe_filename("sdsc-ordes/gimie") == "sdsc-ordes_gimie.json"
    assert _safe_filename("a b/c@d") == "a_b_c_d.json"


# -- step --------------------------------------------------------------------


def test_run_metadata_extractor_writes_one_file_per_repo(tmp_path: Path) -> None:
    crawler_dir = tmp_path / "crawler-json"
    output_dir = tmp_path / "metadata-json"
    _seed_crawler_graph(crawler_dir, ["sdsc-ordes/gimie", "owner/lib"])

    extractor = MagicMock(spec=MetadataExtractorService)
    extractor.fetch_repo_jsonld.side_effect = lambda full, force_refresh=False: _gimie_response(full)

    services = _make_container(extractor)
    ctx = {
        "services": services,
        "step_config": {
            "input_dir": str(crawler_dir),
            "mode": "v1_gimie",
            "input_filename": "crawler-graph.json",
            "output_dir": str(output_dir),
        },
    }

    run_metadata_extractor(ctx)

    assert (output_dir / "sdsc-ordes_gimie.json").is_file()
    assert (output_dir / "owner_lib.json").is_file()
    body = json.loads((output_dir / "sdsc-ordes_gimie.json").read_text(encoding="utf-8"))
    assert body["link"] == "https://github.com/sdsc-ordes/gimie"
    assert extractor.fetch_repo_jsonld.call_count == 2


def test_run_metadata_extractor_skip_existing_default(tmp_path: Path) -> None:
    crawler_dir = tmp_path / "crawler-json"
    output_dir = tmp_path / "metadata-json"
    output_dir.mkdir(parents=True)
    _seed_crawler_graph(crawler_dir, ["sdsc-ordes/gimie"])
    # Pre-existing output file — should be skipped without an HTTP call.
    (output_dir / "sdsc-ordes_gimie.json").write_text("{}", encoding="utf-8")

    extractor = MagicMock(spec=MetadataExtractorService)
    services = _make_container(extractor)
    ctx = {
        "services": services,
        "step_config": {
            "input_dir": str(crawler_dir),
            "mode": "v1_gimie",
            "output_dir": str(output_dir),
            # skip_existing defaults to True
        },
    }

    run_metadata_extractor(ctx)

    extractor.fetch_repo_jsonld.assert_not_called()


def test_run_metadata_extractor_force_refresh_propagates(tmp_path: Path) -> None:
    crawler_dir = tmp_path / "crawler-json"
    output_dir = tmp_path / "metadata-json"
    _seed_crawler_graph(crawler_dir, ["sdsc-ordes/gimie"])

    extractor = MagicMock(spec=MetadataExtractorService)
    extractor.fetch_repo_jsonld.return_value = _gimie_response("sdsc-ordes/gimie")
    services = _make_container(extractor)
    ctx = {
        "services": services,
        "step_config": {
            "input_dir": str(crawler_dir),
            "mode": "v1_gimie",
            "output_dir": str(output_dir),
            "force_refresh": True,
        },
    }

    run_metadata_extractor(ctx)

    extractor.fetch_repo_jsonld.assert_called_once_with(
        "sdsc-ordes/gimie", force_refresh=True
    )


def test_run_metadata_extractor_max_repos_caps_iteration(tmp_path: Path) -> None:
    crawler_dir = tmp_path / "crawler-json"
    output_dir = tmp_path / "metadata-json"
    _seed_crawler_graph(crawler_dir, [f"owner/repo-{i}" for i in range(5)])

    extractor = MagicMock(spec=MetadataExtractorService)
    extractor.fetch_repo_jsonld.side_effect = lambda full, force_refresh=False: _gimie_response(full)
    services = _make_container(extractor)
    ctx = {
        "services": services,
        "step_config": {
            "input_dir": str(crawler_dir),
            "mode": "v1_gimie",
            "output_dir": str(output_dir),
            "max_repos": 2,
        },
    }

    run_metadata_extractor(ctx)

    assert extractor.fetch_repo_jsonld.call_count == 2


def test_run_metadata_extractor_continues_past_individual_failures(
    tmp_path: Path,
) -> None:
    crawler_dir = tmp_path / "crawler-json"
    output_dir = tmp_path / "metadata-json"
    _seed_crawler_graph(crawler_dir, ["good/repo", "bad/repo", "another/good"])

    extractor = MagicMock(spec=MetadataExtractorService)

    def fetch(full: str, force_refresh: bool = False) -> dict[str, Any]:
        if full == "bad/repo":
            raise RuntimeError("HTTP 500")
        return _gimie_response(full)

    extractor.fetch_repo_jsonld.side_effect = fetch
    services = _make_container(extractor)
    ctx = {
        "services": services,
        "step_config": {
            "input_dir": str(crawler_dir),
            "mode": "v1_gimie",
            "output_dir": str(output_dir),
        },
    }

    # Should not raise — one failure is tolerable.
    run_metadata_extractor(ctx)

    assert (output_dir / "good_repo.json").is_file()
    assert (output_dir / "another_good.json").is_file()
    assert not (output_dir / "bad_repo.json").exists()


def test_run_metadata_extractor_raises_when_all_repos_fail(tmp_path: Path) -> None:
    crawler_dir = tmp_path / "crawler-json"
    output_dir = tmp_path / "metadata-json"
    _seed_crawler_graph(crawler_dir, ["bad/one", "bad/two"])

    extractor = MagicMock(spec=MetadataExtractorService)
    extractor.fetch_repo_jsonld.side_effect = RuntimeError("everything is broken")
    services = _make_container(extractor)
    ctx = {
        "services": services,
        "step_config": {
            "input_dir": str(crawler_dir),
            "mode": "v1_gimie",
            "output_dir": str(output_dir),
        },
    }

    with pytest.raises(RuntimeError, match="all 2 repos failed"):
        run_metadata_extractor(ctx)


def test_run_metadata_extractor_no_repos_in_graph(tmp_path: Path) -> None:
    crawler_dir = tmp_path / "crawler-json"
    output_dir = tmp_path / "metadata-json"
    _seed_crawler_graph(crawler_dir, [])

    extractor = MagicMock(spec=MetadataExtractorService)
    services = _make_container(extractor)
    ctx = {
        "services": services,
        "step_config": {
            "input_dir": str(crawler_dir),
            "mode": "v1_gimie",
            "output_dir": str(output_dir),
        },
    }

    # Empty repos dict → log a warning and return; no HTTP calls.
    run_metadata_extractor(ctx)
    extractor.fetch_repo_jsonld.assert_not_called()


def test_run_metadata_extractor_raises_when_graph_missing(tmp_path: Path) -> None:
    extractor = MagicMock(spec=MetadataExtractorService)
    services = _make_container(extractor)
    ctx = {
        "services": services,
        "step_config": {"input_dir": str(tmp_path / "missing")},
    }
    with pytest.raises(FileNotFoundError, match="crawler graph"):
        run_metadata_extractor(ctx)


def test_run_metadata_extractor_requires_services_context() -> None:
    with pytest.raises(RuntimeError, match="ServiceContainer"):
        run_metadata_extractor({"step_config": {}})


# -- v2 mode (default) -------------------------------------------------------


def _v2_response(repo: str) -> dict[str, Any]:
    return {
        "source_url": f"https://github.com/{repo}",
        "detected_type": "repository",
        "output_format": "jsonld",
        "output": {
            "@context": {"schema": "http://schema.org/"},
            "@graph": [
                {
                    "@id": f"https://github.com/{repo}",
                    "@type": ["schema:SoftwareSourceCode"],
                    "schema:name": [{"@value": repo.split("/")[1]}],
                }
            ],
        },
        "stats": {
            "entities_count": 1,
            "triples_count": 2,
            "run_id": "abc",
            "duration_ms": 100,
        },
        "warnings": [],
    }


def test_run_metadata_extractor_uses_v2_by_default(tmp_path: Path) -> None:
    crawler_dir = tmp_path / "crawler-json"
    output_dir = tmp_path / "metadata-json"
    _seed_crawler_graph(crawler_dir, ["sdsc-ordes/gimie"])

    extractor = MagicMock(spec=MetadataExtractorService)
    # Accept ``**kwargs`` so this lambda doesn't have to chase
    # signature additions (``include_internal_fields`` and any future
    # optional knobs) every time the service grows.
    extractor.extract_repo_jsonld_v2.side_effect = (
        lambda full, **kwargs: _v2_response(full)
    )

    services = _make_container(extractor)
    ctx = {
        "services": services,
        "step_config": {
            "input_dir": str(crawler_dir),
            "output_dir": str(output_dir),
            # mode left unset → defaults to "v2"
        },
    }

    run_metadata_extractor(ctx)

    # Default agent_runtime is rule_based — never touches LLM/RCP_TOKEN.
    extractor.extract_repo_jsonld_v2.assert_called_once()
    _, kwargs = extractor.extract_repo_jsonld_v2.call_args
    assert kwargs["agent_runtime"] == "rule_based"
    extractor.fetch_repo_jsonld.assert_not_called()
    body = json.loads((output_dir / "sdsc-ordes_gimie.json").read_text())
    assert body["output_format"] == "jsonld"


def test_run_metadata_extractor_v2_propagates_runtime_and_timeout(
    tmp_path: Path,
) -> None:
    crawler_dir = tmp_path / "crawler-json"
    output_dir = tmp_path / "metadata-json"
    _seed_crawler_graph(crawler_dir, ["a/b"])

    extractor = MagicMock(spec=MetadataExtractorService)
    extractor.extract_repo_jsonld_v2.return_value = _v2_response("a/b")
    services = _make_container(extractor)
    ctx = {
        "services": services,
        "step_config": {
            "input_dir": str(crawler_dir),
            "output_dir": str(output_dir),
            "mode": "v2",
            "v2_agent_runtime": "llm",
            "v2_poll_interval_seconds": 0.5,
            "v2_timeout_seconds": 30.0,
        },
    }

    run_metadata_extractor(ctx)

    _, kwargs = extractor.extract_repo_jsonld_v2.call_args
    assert kwargs["agent_runtime"] == "llm"
    assert kwargs["poll_interval"] == 0.5
    assert kwargs["timeout"] == 30.0


def test_run_metadata_extractor_unknown_mode_raises(tmp_path: Path) -> None:
    crawler_dir = tmp_path / "crawler-json"
    output_dir = tmp_path / "metadata-json"
    _seed_crawler_graph(crawler_dir, ["a/b"])

    extractor = MagicMock(spec=MetadataExtractorService)
    services = _make_container(extractor)
    ctx = {
        "services": services,
        "step_config": {
            "input_dir": str(crawler_dir),
            "output_dir": str(output_dir),
            "mode": "v3_galaxy_brain",
        },
    }

    with pytest.raises(ValueError, match="unknown mode"):
        run_metadata_extractor(ctx)


# -- parallel-mode tests -----------------------------------------------------


def test_run_metadata_extractor_max_workers_runs_in_parallel(tmp_path: Path) -> None:
    """``max_workers > 1`` should overlap v2 calls in time.

    Stubs ``extract_repo_jsonld_v2`` so each call blocks for a fixed
    interval, then asserts the wall-clock time was a small fraction of
    what a sequential run would take.
    """
    import time

    crawler_dir = tmp_path / "crawler-json"
    output_dir = tmp_path / "metadata-json"
    repos = [f"owner/repo-{i:02d}" for i in range(6)]
    _seed_crawler_graph(crawler_dir, repos)

    DELAY = 0.4  # seconds per repo

    def _stub(full_name: str, **_kwargs: Any) -> dict[str, Any]:
        time.sleep(DELAY)
        return {"output": [{"@id": f"https://github.com/{full_name}"}]}

    extractor = MagicMock(spec=MetadataExtractorService)
    extractor.extract_repo_jsonld_v2.side_effect = _stub

    services = _make_container(extractor)
    ctx = {
        "services": services,
        "step_config": {
            "input_dir": str(crawler_dir),
            "output_dir": str(output_dir),
            "mode": "v2",
            "max_workers": 6,
            "v2_poll_interval_seconds": 0.0,
            "v2_timeout_seconds": 5.0,
        },
    }

    started = time.monotonic()
    run_metadata_extractor(ctx)
    elapsed = time.monotonic() - started

    # All 6 files written.
    assert len(list(output_dir.glob("*.json"))) == 6
    # 6 sequential calls would be ≥ 6×DELAY = 2.4s; in parallel it
    # should be ≈ DELAY plus pool overhead. Give a generous ceiling
    # so a slow CI box doesn't flake the test.
    assert elapsed < DELAY * 3, (
        f"parallel pool didn't overlap calls (elapsed={elapsed:.2f}s, "
        f"would be ≥ {6 * DELAY:.2f}s sequential)"
    )


def test_run_metadata_extractor_max_workers_one_is_sequential(tmp_path: Path) -> None:
    """``max_workers=1`` should preserve the old single-threaded behaviour:
    extract_repo_jsonld_v2 is called once per non-skipped repo, no
    counters get lost under contention."""
    crawler_dir = tmp_path / "crawler-json"
    output_dir = tmp_path / "metadata-json"
    _seed_crawler_graph(crawler_dir, [f"o/r{i}" for i in range(4)])

    extractor = MagicMock(spec=MetadataExtractorService)
    extractor.extract_repo_jsonld_v2.side_effect = (
        lambda full, **_kwargs: {"output": [{"@id": f"https://github.com/{full}"}]}
    )

    services = _make_container(extractor)
    ctx = {
        "services": services,
        "step_config": {
            "input_dir": str(crawler_dir),
            "output_dir": str(output_dir),
            "mode": "v2",
            "max_workers": 1,
            "v2_poll_interval_seconds": 0.0,
            "v2_timeout_seconds": 5.0,
        },
    }

    run_metadata_extractor(ctx)

    assert extractor.extract_repo_jsonld_v2.call_count == 4
    assert len(list(output_dir.glob("*.json"))) == 4


def test_run_metadata_extractor_parallel_partial_failure_counted(tmp_path: Path) -> None:
    """Under concurrency every failed repo must end up in the failed
    list, and every success in the success counter — no races."""
    crawler_dir = tmp_path / "crawler-json"
    output_dir = tmp_path / "metadata-json"
    repos = [f"o/r{i:02d}" for i in range(8)]
    _seed_crawler_graph(crawler_dir, repos)

    def _stub(full_name: str, **_kwargs: Any) -> dict[str, Any]:
        # Half the repos raise; half succeed.
        idx = int(full_name.rsplit("r", 1)[-1])
        if idx % 2 == 0:
            raise RuntimeError(f"boom on {full_name}")
        return {"output": [{"@id": f"https://github.com/{full_name}"}]}

    extractor = MagicMock(spec=MetadataExtractorService)
    extractor.extract_repo_jsonld_v2.side_effect = _stub

    services = _make_container(extractor)
    ctx = {
        "services": services,
        "step_config": {
            "input_dir": str(crawler_dir),
            "output_dir": str(output_dir),
            "mode": "v2",
            "max_workers": 4,
            "v2_poll_interval_seconds": 0.0,
            "v2_timeout_seconds": 5.0,
        },
    }

    run_metadata_extractor(ctx)

    # 4 of 8 repos write files; the other 4 fail and don't.
    written = sorted(p.stem for p in output_dir.glob("*.json"))
    assert written == ["o_r01", "o_r03", "o_r05", "o_r07"]
