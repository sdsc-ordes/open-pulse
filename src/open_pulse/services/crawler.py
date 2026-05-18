"""HTTP client for the Open Pulse Crawler API.

Wraps the FastAPI service published as
``ghcr.io/sdsc-ordes/open-pulse-crawler``.  The crawler exposes a
submit/poll/fetch flow:

1. ``POST /api/v1/crawl`` returns a ``job_id`` (202 Accepted).
2. ``GET  /api/v1/crawl/{job_id}`` returns the current status.
3. ``GET  /api/v1/graph/{job_id}`` returns the crawl result, only once the
   status is ``COMPLETED`` (otherwise 409).

The Bearer token is resolved from the environment at call time (via the
``api_token_env`` name) so that ``open-pulse health`` can construct a
service even when no token is configured — the unauthenticated
``/api/v1/health`` endpoint still works.
"""

from __future__ import annotations

import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 60.0
_DEFAULT_POLL_INTERVAL = 5.0
_DEFAULT_TIMEOUT = 3600.0
# When the crawler is saturated (e.g. multiple concurrent jobs chewing
# through large queues) status GETs can stall well past the per-request
# timeout. A single ReadTimeout shouldn't blow up an in-flight pipeline
# step — the job on the other side is still alive. Tolerate a small
# burst of transient HTTP errors before giving up.
_MAX_TRANSIENT_POLL_FAILURES = 5


class CrawlerJobFailedError(RuntimeError):
    """Raised when a crawler job ends in the ``FAILED`` state."""


class CrawlerJobTimeoutError(TimeoutError):
    """Raised when ``wait_for_completion`` exceeds its deadline."""


class CrawlerService:
    """Lightweight HTTP client for the Open Pulse Crawler API."""

    def __init__(self, endpoint: str, api_token_env: str) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_token_env = api_token_env
        self._client = httpx.Client(timeout=_HTTP_TIMEOUT)

    # -- Auth --------------------------------------------------------------

    def _bearer_headers(self) -> dict[str, str]:
        token = os.environ.get(self.api_token_env, "")
        if not token:
            raise RuntimeError(
                f"{self.api_token_env} is not set in the environment; "
                "cannot call authenticated crawler endpoints."
            )
        return {"Authorization": f"Bearer {token}"}

    # -- Probes ------------------------------------------------------------

    def check_health(self) -> tuple[bool, str]:
        """Probe ``GET /api/v1/health`` (no auth required)."""
        url = f"{self.endpoint}/api/v1/health"
        try:
            resp = self._client.get(url)
        except httpx.HTTPError as exc:
            return False, str(exc)
        if resp.status_code == 200:
            return True, f"HTTP {resp.status_code}"
        return False, f"HTTP {resp.status_code}"

    # -- Job lifecycle -----------------------------------------------------

    def submit_crawl(self, request: dict[str, object]) -> str:
        """Start a crawl job and return its ``job_id``."""
        url = f"{self.endpoint}/api/v1/crawl"
        resp = self._client.post(url, json=request, headers=self._bearer_headers())
        if resp.status_code != 202:
            raise RuntimeError(
                f"crawler submit failed: HTTP {resp.status_code} — {resp.text}"
            )
        body = resp.json()
        return str(body["job_id"])

    def get_status(self, job_id: str) -> dict[str, object]:
        """Return the current status payload for *job_id*."""
        url = f"{self.endpoint}/api/v1/crawl/{job_id}"
        resp = self._client.get(url, headers=self._bearer_headers())
        if resp.status_code != 200:
            raise RuntimeError(
                f"crawler status failed: HTTP {resp.status_code} — {resp.text}"
            )
        return resp.json()

    def get_graph(self, job_id: str) -> dict[str, object]:
        """Return the full graph payload for *job_id* (must be COMPLETED)."""
        url = f"{self.endpoint}/api/v1/graph/{job_id}"
        resp = self._client.get(url, headers=self._bearer_headers())
        if resp.status_code != 200:
            raise RuntimeError(
                f"crawler graph fetch failed: HTTP {resp.status_code} — {resp.text}"
            )
        body = resp.json()
        graph = body.get("graph")
        if not isinstance(graph, dict):
            raise RuntimeError(
                f"crawler graph response missing 'graph' object: {body!r}"
            )
        return graph

    def wait_for_completion(
        self,
        job_id: str,
        *,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> dict[str, object]:
        """Poll ``get_status`` until the job leaves a non-terminal state.

        Returns the final status dict on ``COMPLETED``.  Raises
        :class:`CrawlerJobFailedError` on ``FAILED`` and
        :class:`CrawlerJobTimeoutError` if the deadline elapses first.
        """
        deadline = time.monotonic() + timeout
        consecutive_failures = 0
        while True:
            try:
                status = self.get_status(job_id)
            except httpx.HTTPError as exc:
                # Transient network/timeout errors during polling are common
                # when the crawler is busy. Treat the first few as warnings
                # and keep polling — the job is still running on the other
                # side. Only escalate when we've burned through the budget.
                consecutive_failures += 1
                logger.warning(
                    "crawler: poll for job %s failed (%d/%d): %s",
                    job_id,
                    consecutive_failures,
                    _MAX_TRANSIENT_POLL_FAILURES,
                    exc,
                )
                if consecutive_failures >= _MAX_TRANSIENT_POLL_FAILURES:
                    raise
                if time.monotonic() >= deadline:
                    raise CrawlerJobTimeoutError(
                        f"crawler job {job_id} did not complete within {timeout:.0f}s "
                        f"(last poll error: {exc})"
                    ) from exc
                time.sleep(poll_interval)
                continue

            consecutive_failures = 0
            state = status.get("status")
            if state == "completed":
                return status
            if state == "failed":
                detail = status.get("detail") or "no detail provided"
                raise CrawlerJobFailedError(f"crawler job {job_id} failed: {detail}")
            if state == "cancelled":
                # A cancel from the crawler-side (Pause/Stop button or
                # API call) is an authoritative stop signal. Surface it
                # like a failure so the runner doesn't poll the dead job
                # for the rest of the timeout window.
                detail = status.get("detail") or "cancelled by operator"
                raise CrawlerJobFailedError(f"crawler job {job_id} cancelled: {detail}")
            if time.monotonic() >= deadline:
                raise CrawlerJobTimeoutError(
                    f"crawler job {job_id} did not complete within {timeout:.0f}s "
                    f"(last state: {state!r})"
                )
            time.sleep(poll_interval)

    # -- Lifecycle ---------------------------------------------------------

    def close(self) -> None:
        """Release the underlying HTTP client."""
        self._client.close()
