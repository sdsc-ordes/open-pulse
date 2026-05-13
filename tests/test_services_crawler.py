"""Tests for ``open_pulse.services.crawler``."""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest

from open_pulse.services.config import (
    DEFAULT_CRAWLER_API_TOKEN_ENV,
    DEFAULT_CRAWLER_ENDPOINT,
    CrawlerServiceConfig,
    ServicesConfig,
)
from open_pulse.services.container import ServiceContainer
from open_pulse.services.crawler import (
    CrawlerJobFailedError,
    CrawlerJobTimeoutError,
    CrawlerService,
)


def _service(transport: httpx.MockTransport, *, env: str = "TEST_TOKEN") -> CrawlerService:
    svc = CrawlerService(endpoint="http://crawler:8000", api_token_env=env)
    # Replace the inner client with one bound to our mock transport.
    svc._client.close()
    svc._client = httpx.Client(transport=transport, timeout=5.0)
    return svc


# -- check_health -------------------------------------------------------------


def test_check_health_returns_true_on_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/health"
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={"status": "ok", "version": "0.1.0"})

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


# -- submit_crawl -------------------------------------------------------------


def test_submit_crawl_returns_job_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "secret-value")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/crawl"
        assert request.headers["Authorization"] == "Bearer secret-value"
        body = json.loads(request.content)
        assert body["seeds"] == ["sdsc-ordes/open-pulse"]
        return httpx.Response(202, json={"job_id": "abc-123", "status": "pending"})

    svc = _service(httpx.MockTransport(handler))
    job_id = svc.submit_crawl({"seeds": ["sdsc-ordes/open-pulse"], "max_rounds": 1})
    assert job_id == "abc-123"


def test_submit_crawl_raises_when_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_TOKEN", raising=False)

    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover - never called
        raise AssertionError("HTTP must not be called when token is missing")

    svc = _service(httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="TEST_TOKEN is not set"):
        svc.submit_crawl({"seeds": ["x"]})


def test_submit_crawl_raises_on_non_202(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "x")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Invalid token")

    svc = _service(httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="HTTP 401"):
        svc.submit_crawl({"seeds": ["x"]})


# -- wait_for_completion ------------------------------------------------------


def _status_sequence_handler(states: Iterator[str]) -> httpx.MockTransport:
    """Build a transport whose status responses cycle through *states*."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.startswith("/api/v1/crawl/")
        state = next(states)
        body: dict[str, object] = {"job_id": "job-1", "status": state}
        if state == "failed":
            body["detail"] = "github rate limit exhausted"
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def test_wait_for_completion_returns_on_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "x")
    states = iter(["pending", "running", "completed"])
    svc = _service(_status_sequence_handler(states))

    final = svc.wait_for_completion("job-1", poll_interval=0.0, timeout=5.0)
    assert final["status"] == "completed"


def test_wait_for_completion_raises_on_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "x")
    states = iter(["pending", "failed"])
    svc = _service(_status_sequence_handler(states))

    with pytest.raises(CrawlerJobFailedError, match="rate limit"):
        svc.wait_for_completion("job-1", poll_interval=0.0, timeout=5.0)


def test_wait_for_completion_raises_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "x")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"job_id": "job-1", "status": "running"})

    svc = _service(httpx.MockTransport(handler))
    with pytest.raises(CrawlerJobTimeoutError):
        svc.wait_for_completion("job-1", poll_interval=0.0, timeout=0.0)


# -- get_graph ----------------------------------------------------------------


def test_get_graph_returns_inner_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "x")
    payload = {
        "job_id": "job-1",
        "graph": {"users": [{"login": "alice"}], "orgs": [], "repos": []},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/graph/job-1"
        return httpx.Response(200, json=payload)

    svc = _service(httpx.MockTransport(handler))
    graph = svc.get_graph("job-1")
    assert graph == payload["graph"]


def test_get_graph_raises_on_409(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "x")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, text="Job is not completed")

    svc = _service(httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="HTTP 409"):
        svc.get_graph("job-1")


# -- ServicesConfig / ServiceContainer wiring --------------------------------


def test_services_config_defaults_include_crawler() -> None:
    cfg = ServicesConfig()
    assert isinstance(cfg.crawler, CrawlerServiceConfig)
    assert cfg.crawler.endpoint == DEFAULT_CRAWLER_ENDPOINT
    assert cfg.crawler.api_token_env == DEFAULT_CRAWLER_API_TOKEN_ENV


def test_service_container_builds_crawler() -> None:
    container = ServiceContainer.from_services_config(ServicesConfig())
    try:
        assert container.crawler.endpoint == DEFAULT_CRAWLER_ENDPOINT
        assert container.crawler.api_token_env == DEFAULT_CRAWLER_API_TOKEN_ENV
    finally:
        container.close_all()
