"""Tests for ``open_pulse.services.metadata_extractor``."""

from __future__ import annotations

import json

import httpx
import pytest

from open_pulse.services.config import (
    DEFAULT_METADATA_EXTRACTOR_ENDPOINT,
    MetadataExtractorServiceConfig,
    ServicesConfig,
)
from open_pulse.services.container import ServiceContainer
from open_pulse.services.metadata_extractor import (
    ExtractJobFailedError,
    ExtractJobTimeoutError,
    MetadataExtractorService,
    _normalize_repo_url,
)


def _service(transport: httpx.MockTransport) -> MetadataExtractorService:
    svc = MetadataExtractorService(endpoint="http://gme:1234")
    svc._client.close()
    svc._client = httpx.Client(transport=transport, timeout=5.0)
    return svc


# -- _normalize_repo_url ------------------------------------------------------


def test_normalize_accepts_owner_repo_slug() -> None:
    assert _normalize_repo_url("sdsc-ordes/gimie") == "https://github.com/sdsc-ordes/gimie"


def test_normalize_accepts_full_https_url() -> None:
    assert _normalize_repo_url("https://github.com/sdsc-ordes/gimie") == "https://github.com/sdsc-ordes/gimie"


def test_normalize_strips_trailing_slash_and_whitespace() -> None:
    assert _normalize_repo_url("  sdsc-ordes/gimie/  ") == "https://github.com/sdsc-ordes/gimie"


# -- check_health -------------------------------------------------------------


def test_check_health_returns_true_on_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/"
        return httpx.Response(200, json={"title": "Hello, welcome to GME 2.0"})

    svc = _service(httpx.MockTransport(handler))
    ok, detail = svc.check_health()
    assert ok is True
    assert "200" in detail


def test_check_health_returns_false_on_connection_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    svc = _service(httpx.MockTransport(handler))
    ok, detail = svc.check_health()
    assert ok is False
    assert "refused" in detail


# -- fetch_repo_jsonld --------------------------------------------------------


_SAMPLE_GIMIE_RESPONSE = {
    "link": "https://github.com/sdsc-ordes/gimie",
    "type": "repository",
    "parsedTimestamp": "2026-05-03T00:00:00",
    "output": [
        {
            "@id": "https://github.com/sdsc-ordes/gimie",
            "@type": ["http://schema.org/SoftwareSourceCode"],
            "http://schema.org/name": [{"@value": "gimie"}],
        }
    ],
    "stats": {"duration": 1.23, "status_code": 200},
}


def test_fetch_repo_jsonld_returns_full_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # Path: /v1/repository/gimie/json-ld/<percent-encoded-url>
        assert request.url.path.startswith("/v1/repository/gimie/json-ld/")
        # force_refresh defaults to false
        assert request.url.params.get("force_refresh") == "false"
        return httpx.Response(200, json=_SAMPLE_GIMIE_RESPONSE)

    svc = _service(httpx.MockTransport(handler))
    body = svc.fetch_repo_jsonld("sdsc-ordes/gimie")
    assert body == _SAMPLE_GIMIE_RESPONSE
    assert isinstance(body["output"], list)


def test_fetch_repo_jsonld_force_refresh_query_param() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("force_refresh") == "true"
        return httpx.Response(200, json=_SAMPLE_GIMIE_RESPONSE)

    svc = _service(httpx.MockTransport(handler))
    svc.fetch_repo_jsonld("sdsc-ordes/gimie", force_refresh=True)


def test_fetch_repo_jsonld_raises_on_non_200() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="GitHub token not configured")

    svc = _service(httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="HTTP 401"):
        svc.fetch_repo_jsonld("sdsc-ordes/gimie")


def test_fetch_repo_jsonld_raises_on_network_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    svc = _service(httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="HTTP error fetching"):
        svc.fetch_repo_jsonld("sdsc-ordes/gimie")


def test_fetch_repo_jsonld_raises_when_response_is_not_object() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "an", "object"])

    svc = _service(httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="not a JSON object"):
        svc.fetch_repo_jsonld("sdsc-ordes/gimie")


# -- ServicesConfig / ServiceContainer wiring --------------------------------


def test_services_config_defaults_include_metadata_extractor() -> None:
    cfg = ServicesConfig()
    assert isinstance(cfg.metadata_extractor, MetadataExtractorServiceConfig)
    assert cfg.metadata_extractor.endpoint == DEFAULT_METADATA_EXTRACTOR_ENDPOINT


# -- v2 async path ------------------------------------------------------------


_V2_RESPONSE_BODY = {
    "source_url": "https://github.com/sdsc-ordes/gimie",
    "detected_type": "repository",
    "output_format": "jsonld",
    "output": {
        "@context": {"schema": "http://schema.org/"},
        "@graph": [{"@id": "https://github.com/sdsc-ordes/gimie"}],
    },
    "stats": {"entities_count": 1, "triples_count": 1, "run_id": "x", "duration_ms": 1},
    "warnings": [],
}


def test_submit_extract_returns_job_id_with_rule_based_default() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            202,
            json={
                "job_id": "v2-job-1",
                "status": "pending",
                "status_url": "/v2/jobs/v2-job-1",
                "submitted_at": "2026-05-03T00:00:00Z",
            },
        )

    svc = _service(httpx.MockTransport(handler))
    job_id = svc.submit_extract("sdsc-ordes/gimie")

    assert job_id == "v2-job-1"
    assert captured["url"].endswith("/v2/extract")
    assert captured["body"] == {
        "source_url": "https://github.com/sdsc-ordes/gimie",
        "output_format": "jsonld",
        "agent_runtime": "rule_based",
        # New extractor :develop knob — defaults to False; the GME
        # strips ``_``-prefixed internal fields unless asked.
        "include_internal_fields": False,
    }


def test_submit_extract_raises_on_non_202() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text="bad url")

    svc = _service(httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="HTTP 422"):
        svc.submit_extract("sdsc-ordes/gimie")


def test_wait_for_extract_returns_on_completed() -> None:
    states = iter(["pending", "running", "completed"])

    def handler(_r: httpx.Request) -> httpx.Response:
        state = next(states)
        body: dict = {
            "job_id": "x",
            "status": state,
            "request": {
                "source_url": "https://github.com/x/y",
                "output_format": "jsonld",
            },
            "submitted_at": "2026-05-03T00:00:00Z",
        }
        if state == "completed":
            body["result"] = _V2_RESPONSE_BODY
        return httpx.Response(200, json=body)

    svc = _service(httpx.MockTransport(handler))
    job = svc.wait_for_extract("x", poll_interval=0.0, timeout=5.0)
    assert job["status"] == "completed"
    assert job["result"] == _V2_RESPONSE_BODY


def test_wait_for_extract_raises_on_failed() -> None:
    seq = iter(
        [
            ("pending", None),
            ("failed", {"detail": "agent crashed"}),
        ]
    )

    def handler(_r: httpx.Request) -> httpx.Response:
        state, error = next(seq)
        body = {
            "job_id": "x",
            "status": state,
            "request": {"source_url": "https://github.com/x/y", "output_format": "jsonld"},
            "submitted_at": "2026-05-03T00:00:00Z",
        }
        if error:
            body["error"] = error
        return httpx.Response(200, json=body)

    svc = _service(httpx.MockTransport(handler))
    with pytest.raises(ExtractJobFailedError, match="agent crashed"):
        svc.wait_for_extract("x", poll_interval=0.0, timeout=5.0)


def test_wait_for_extract_raises_on_timeout() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "job_id": "x",
                "status": "running",
                "request": {"source_url": "https://github.com/x/y", "output_format": "jsonld"},
                "submitted_at": "2026-05-03T00:00:00Z",
            },
        )

    svc = _service(httpx.MockTransport(handler))
    with pytest.raises(ExtractJobTimeoutError):
        svc.wait_for_extract("x", poll_interval=0.0, timeout=0.0)


def test_extract_repo_jsonld_v2_one_shot_returns_result_body() -> None:
    """submit + poll + return ``result`` in one call."""

    state = {"step": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/extract":
            return httpx.Response(
                202,
                json={
                    "job_id": "v2-1",
                    "status": "pending",
                    "status_url": "/v2/jobs/v2-1",
                    "submitted_at": "2026-05-03T00:00:00Z",
                },
            )
        # Polling /v2/jobs/v2-1 — first response running, second completed.
        state["step"] += 1
        body: dict = {
            "job_id": "v2-1",
            "status": "completed" if state["step"] >= 2 else "running",
            "request": {
                "source_url": "https://github.com/sdsc-ordes/gimie",
                "output_format": "jsonld",
            },
            "submitted_at": "2026-05-03T00:00:00Z",
        }
        if state["step"] >= 2:
            body["result"] = _V2_RESPONSE_BODY
        return httpx.Response(200, json=body)

    svc = _service(httpx.MockTransport(handler))
    result = svc.extract_repo_jsonld_v2(
        "sdsc-ordes/gimie", poll_interval=0.0, timeout=5.0
    )
    assert result == _V2_RESPONSE_BODY


# -- ServicesConfig / ServiceContainer wiring --------------------------------


def test_service_container_builds_metadata_extractor() -> None:
    container = ServiceContainer.from_services_config(ServicesConfig())
    try:
        assert container.metadata_extractor.endpoint == DEFAULT_METADATA_EXTRACTOR_ENDPOINT
    finally:
        container.close_all()
