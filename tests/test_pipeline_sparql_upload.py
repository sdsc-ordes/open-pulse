"""Tests for ``open_pulse.services.sparql_store.upload`` and the pipeline step."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from open_pulse.pipeline.sparql_upload import run_sparql_upload
from open_pulse.services.container import ServiceContainer
from open_pulse.services.crawler import CrawlerService
from open_pulse.services.metadata_extractor import MetadataExtractorService
from open_pulse.services.neo4j import Neo4jService
from open_pulse.services.sparql_store import (
    SparqlStoreService,
    _jsonld_to_ntriples,
    _normalize_jsonld,
)


def _service(transport: httpx.MockTransport) -> SparqlStoreService:
    svc = SparqlStoreService(endpoint="http://sparql:7878", auth_env="TEST_SPARQL_AUTH")
    # Replace the lazy client with one bound to our mock transport.
    svc._client = httpx.Client(transport=transport, timeout=5.0)
    return svc


def _make_container(sparql_store: SparqlStoreService) -> ServiceContainer:
    return ServiceContainer(
        neo4j=Neo4jService(endpoint="bolt://localhost:7687"),
        sparql_store=sparql_store,
        crawler=CrawlerService(
            endpoint="http://localhost:8000",
            api_token_env="CRAWLER_API_TOKEN",
        ),
        metadata_extractor=MetadataExtractorService(endpoint="http://localhost:1234"),
    )


def _gimie_payload() -> dict[str, Any]:
    return {
        "link": "https://github.com/sdsc-ordes/gimie",
        "type": "repository",
        "output": [
            {
                "@id": "https://github.com/sdsc-ordes/gimie",
                "@type": ["http://schema.org/SoftwareSourceCode"],
                "http://schema.org/name": [{"@value": "gimie"}],
            }
        ],
    }


# -- helpers ------------------------------------------------------------------


def test_normalize_unwraps_gimie_envelope() -> None:
    payload = _gimie_payload()
    norm = _normalize_jsonld(payload)
    assert norm == {"@graph": payload["output"]}


def test_normalize_passes_through_other_shapes() -> None:
    payload: dict[str, Any] = {"@id": "http://x", "@type": "y"}
    assert _normalize_jsonld(payload) is payload


def test_jsonld_to_ntriples_emits_triples() -> None:
    nt = _jsonld_to_ntriples({"@graph": _gimie_payload()["output"]})
    text = nt.decode("utf-8")
    assert "<https://github.com/sdsc-ordes/gimie>" in text
    assert "<http://schema.org/name>" in text
    assert text.count("\n") >= 2  # type triple + name triple


# -- SparqlStoreService.upload (mocked transport) --------------------------------


def test_upload_sends_ntriples_with_basic_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_SPARQL_AUTH", "pipeline/secret")

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["content_type"] = request.headers.get("content-type")
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = request.content
        return httpx.Response(204)

    svc = _service(httpx.MockTransport(handler))
    triples = svc.upload(_gimie_payload())

    assert triples >= 2
    assert "/store" in captured["url"]
    assert "default" in captured["url"]
    assert captured["content_type"] == "application/n-triples"
    assert captured["auth"].startswith("Basic ")
    assert b"<https://github.com/sdsc-ordes/gimie>" in captured["body"]


def test_upload_uses_named_graph_when_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_SPARQL_AUTH", "pipeline/secret")

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(204)

    svc = _service(httpx.MockTransport(handler))
    svc.upload(_gimie_payload(), named_graph="https://example.org/g")

    assert "graph=" in captured["url"]
    assert "default" not in captured["url"].split("?", 1)[1]


def test_upload_raises_when_auth_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_SPARQL_AUTH", raising=False)
    svc = _service(httpx.MockTransport(lambda r: httpx.Response(204)))
    with pytest.raises(RuntimeError, match="TEST_SPARQL_AUTH"):
        svc.upload(_gimie_payload())


def test_upload_raises_on_non_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_SPARQL_AUTH", "pipeline/secret")
    svc = _service(httpx.MockTransport(lambda r: httpx.Response(500, text="boom")))
    with pytest.raises(RuntimeError, match="HTTP 500"):
        svc.upload(_gimie_payload())


def test_upload_skips_zero_triple_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty graph → no HTTP call at all (also: no auth required)."""
    monkeypatch.delenv("TEST_SPARQL_AUTH", raising=False)
    called: list[bool] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        called.append(True)
        return httpx.Response(204)

    svc = _service(httpx.MockTransport(handler))
    n = svc.upload({"output": []})
    assert n == 0
    assert called == []


# -- pipeline step -----------------------------------------------------------


def _seed_metadata_dir(d: Path, payloads: dict[str, dict[str, Any]]) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for name, payload in payloads.items():
        (d / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_run_sparql_upload_iterates_each_file(tmp_path: Path) -> None:
    metadata_dir = tmp_path / "metadata-json"
    _seed_metadata_dir(
        metadata_dir,
        {"a_b": _gimie_payload(), "c_d": _gimie_payload()},
    )

    sparql_store = MagicMock(spec=SparqlStoreService)
    sparql_store.upload.return_value = 5
    services = _make_container(sparql_store)
    ctx = {
        "services": services,
        "step_config": {"input_dir": str(metadata_dir)},
    }

    run_sparql_upload(ctx)

    assert sparql_store.upload.call_count == 2


def test_run_sparql_upload_passes_named_graph(tmp_path: Path) -> None:
    metadata_dir = tmp_path / "metadata-json"
    _seed_metadata_dir(metadata_dir, {"a_b": _gimie_payload()})

    sparql_store = MagicMock(spec=SparqlStoreService)
    sparql_store.upload.return_value = 1
    services = _make_container(sparql_store)
    ctx = {
        "services": services,
        "step_config": {
            "input_dir": str(metadata_dir),
            "named_graph": "https://example.org/g",
        },
    }

    run_sparql_upload(ctx)

    sparql_store.upload.assert_called_once()
    _, kwargs = sparql_store.upload.call_args
    assert kwargs["named_graph"] == "https://example.org/g"


def test_run_sparql_upload_auto_named_graph_derives_monthly_uri(
    tmp_path: Path,
) -> None:
    metadata_dir = tmp_path / "metadata-json"
    _seed_metadata_dir(metadata_dir, {"a_b": _gimie_payload()})

    sparql_store = MagicMock(spec=SparqlStoreService)
    sparql_store.upload.return_value = 1
    services = _make_container(sparql_store)
    ctx = {
        "services": services,
        "step_config": {
            "input_dir": str(metadata_dir),
            "auto_named_graph": True,
            "runtime": "hybrid",
        },
    }

    run_sparql_upload(ctx)

    _, kwargs = sparql_store.upload.call_args
    # 2026-MM style suffix; we just sanity-check the shape rather than
    # pinning the calendar.
    assert kwargs["named_graph"].startswith("https://open-pulse.epfl.ch/graph/")
    assert kwargs["named_graph"].endswith("/hybrid")


def test_run_sparql_upload_explicit_named_graph_overrides_auto(
    tmp_path: Path,
) -> None:
    metadata_dir = tmp_path / "metadata-json"
    _seed_metadata_dir(metadata_dir, {"a_b": _gimie_payload()})

    sparql_store = MagicMock(spec=SparqlStoreService)
    sparql_store.upload.return_value = 1
    services = _make_container(sparql_store)
    ctx = {
        "services": services,
        "step_config": {
            "input_dir": str(metadata_dir),
            "named_graph": "https://example.org/literal",
            "auto_named_graph": True,
            "runtime": "hybrid",
        },
    }

    run_sparql_upload(ctx)

    _, kwargs = sparql_store.upload.call_args
    assert kwargs["named_graph"] == "https://example.org/literal"


def test_run_sparql_upload_publishes_hybrid_to_default(tmp_path: Path) -> None:
    metadata_dir = tmp_path / "metadata-json"
    _seed_metadata_dir(metadata_dir, {"a_b": _gimie_payload()})

    sparql_store = MagicMock(spec=SparqlStoreService)
    sparql_store.upload.return_value = 1
    services = _make_container(sparql_store)
    ctx = {
        "services": services,
        "step_config": {
            "input_dir": str(metadata_dir),
            "auto_named_graph": True,
            "runtime": "hybrid",
        },
    }

    run_sparql_upload(ctx)

    # hybrid is in DEFAULT_RUNTIME_PUBLISHES_TO_DEFAULT, so the named
    # graph should be COPYd to the default after upload.
    sparql_store.copy_to_default.assert_called_once()
    (called_graph,), _ = sparql_store.copy_to_default.call_args
    assert called_graph.endswith("/hybrid")


def test_run_sparql_upload_skips_publish_for_non_hybrid(tmp_path: Path) -> None:
    metadata_dir = tmp_path / "metadata-json"
    _seed_metadata_dir(metadata_dir, {"a_b": _gimie_payload()})

    sparql_store = MagicMock(spec=SparqlStoreService)
    sparql_store.upload.return_value = 1
    services = _make_container(sparql_store)
    ctx = {
        "services": services,
        "step_config": {
            "input_dir": str(metadata_dir),
            "auto_named_graph": True,
            "runtime": "rule_based",
        },
    }

    run_sparql_upload(ctx)

    sparql_store.copy_to_default.assert_not_called()


def test_run_sparql_upload_publish_to_default_explicit_override(
    tmp_path: Path,
) -> None:
    metadata_dir = tmp_path / "metadata-json"
    _seed_metadata_dir(metadata_dir, {"a_b": _gimie_payload()})

    sparql_store = MagicMock(spec=SparqlStoreService)
    sparql_store.upload.return_value = 1
    services = _make_container(sparql_store)
    ctx = {
        "services": services,
        "step_config": {
            "input_dir": str(metadata_dir),
            "auto_named_graph": True,
            "runtime": "rule_based",
            "publish_to_default": True,  # override the auto-no
        },
    }

    run_sparql_upload(ctx)

    sparql_store.copy_to_default.assert_called_once()


def test_run_sparql_upload_continues_past_individual_failures(
    tmp_path: Path,
) -> None:
    metadata_dir = tmp_path / "metadata-json"
    _seed_metadata_dir(
        metadata_dir,
        {"good": _gimie_payload(), "bad": _gimie_payload()},
    )

    sparql_store = MagicMock(spec=SparqlStoreService)
    sparql_store.upload.side_effect = [3, RuntimeError("HTTP 500")]
    services = _make_container(sparql_store)
    ctx = {
        "services": services,
        "step_config": {"input_dir": str(metadata_dir)},
    }

    # 1 of 2 succeeds — should not raise.
    run_sparql_upload(ctx)


def test_run_sparql_upload_raises_when_all_fail(tmp_path: Path) -> None:
    metadata_dir = tmp_path / "metadata-json"
    _seed_metadata_dir(
        metadata_dir,
        {"a": _gimie_payload(), "b": _gimie_payload()},
    )

    sparql_store = MagicMock(spec=SparqlStoreService)
    sparql_store.upload.side_effect = RuntimeError("everything is broken")
    services = _make_container(sparql_store)
    ctx = {
        "services": services,
        "step_config": {"input_dir": str(metadata_dir)},
    }

    with pytest.raises(RuntimeError, match="all 2 files failed"):
        run_sparql_upload(ctx)


def test_run_sparql_upload_handles_empty_dir(tmp_path: Path) -> None:
    metadata_dir = tmp_path / "metadata-json"
    metadata_dir.mkdir()

    sparql_store = MagicMock(spec=SparqlStoreService)
    services = _make_container(sparql_store)
    ctx = {
        "services": services,
        "step_config": {"input_dir": str(metadata_dir)},
    }

    run_sparql_upload(ctx)
    sparql_store.upload.assert_not_called()


def test_run_sparql_upload_raises_when_dir_missing(tmp_path: Path) -> None:
    sparql_store = MagicMock(spec=SparqlStoreService)
    services = _make_container(sparql_store)
    ctx = {
        "services": services,
        "step_config": {"input_dir": str(tmp_path / "missing")},
    }
    with pytest.raises(FileNotFoundError, match="metadata-json directory"):
        run_sparql_upload(ctx)


def test_run_sparql_upload_requires_services_context() -> None:
    with pytest.raises(RuntimeError, match="ServiceContainer"):
        run_sparql_upload({"step_config": {}})
