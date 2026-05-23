"""HTTP client for the git-metadata-extractor service.

Wraps the FastAPI service published as
``ghcr.io/imaging-plaza/git-metadata-extractor`` (port 1234 by default).
Speaks two endpoints:

- **v1 (sync gimie-only)**: ``GET /v1/repository/gimie/json-ld/{url}`` —
  rule-based, returns raw gimie expanded JSON-LD synchronously.
- **v2 (async multi-stage)**: ``POST /v2/extract`` + ``GET /v2/jobs/{id}``
  — supports a ``rule_based`` ``agent_runtime`` so we don't need the LLM
  ``RCP_TOKEN``; emits framed JSON-LD with explicit ``@context`` + ``@graph``.

Auth model: the GME server reads ``GITHUB_TOKEN`` (and optionally
``RCP_TOKEN`` for LLM mode) from its own environment. Newer versions also
gate every endpoint behind a Bearer token (``API_TOKEN`` on the server).
This client sends ``Authorization: Bearer <env>`` when ``api_token_env``
resolves to a non-empty value, and omits the header otherwise so older
auth-free deploys keep working.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 120.0  # rule-based extraction can take 30s+ on fresh fetches
# When the extractor is in the middle of a long LLM call, idle status polls
# get dropped with `Server disconnected` / `Connection reset`. The job
# itself is still alive on the extractor side — same pattern we fixed in
# services/crawler.py. Tolerate a small burst before giving up.
_MAX_TRANSIENT_POLL_FAILURES = 5
_GIMIE_PATH = "/v1/repository/gimie/json-ld"
_V2_EXTRACT_PATH = "/v2/extract"
_V2_JOB_PATH = "/v2/jobs"
_DEFAULT_V2_POLL_INTERVAL = 2.0
_DEFAULT_V2_TIMEOUT = 600.0


class ExtractJobFailedError(RuntimeError):
    """Raised when a v2 extract job ends in the ``FAILED`` state."""


class ExtractJobTimeoutError(TimeoutError):
    """Raised when ``wait_for_extract`` exceeds its deadline."""


class MetadataExtractorService:
    """Lightweight HTTP client for the git-metadata-extractor API."""

    def __init__(self, endpoint: str, api_token_env: str | None = None) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_token_env = api_token_env
        self._client = httpx.Client(timeout=_HTTP_TIMEOUT)

    def _auth_headers(self) -> dict[str, str]:
        """Bearer headers when the configured env var resolves; else empty."""
        if not self.api_token_env:
            return {}
        token = os.environ.get(self.api_token_env, "").strip()
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}

    # -- Health probe ------------------------------------------------------

    def check_health(self) -> tuple[bool, str]:
        """Probe ``GET /`` (no auth needed). Returns ``(reachable, detail)``.

        GME doesn't expose a dedicated ``/healthz`` — the root endpoint
        returns the API banner with version + gimie version, which is
        sufficient to confirm liveness.
        """
        url = self.endpoint + "/"
        try:
            resp = self._client.get(url)
        except httpx.HTTPError as exc:
            return False, str(exc)
        if resp.status_code == 200:
            return True, f"HTTP {resp.status_code}"
        return False, f"HTTP {resp.status_code}"

    # -- JSON-LD fetch -----------------------------------------------------

    def fetch_repo_jsonld(
        self,
        full_path: str,
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Fetch the gimie JSON-LD payload for *full_path*.

        Parameters
        ----------
        full_path:
            Either a bare ``owner/repo`` slug (e.g. ``sdsc-ordes/gimie``) or
            a full HTTPS GitHub URL. The endpoint accepts both — bare slugs
            are auto-prefixed with ``https://github.com/``.
        force_refresh:
            When ``True``, bypass GME's server-side cache (1-day TTL).

        Returns the full GME response dict, including the top-level
        ``link`` / ``type`` / ``parsedTimestamp`` / ``output`` / ``stats``
        keys. The graph entities live under ``output`` as a list of
        schema.org-typed JSON-LD nodes.

        Raises ``RuntimeError`` on non-2xx responses.
        """
        repo_url = _normalize_repo_url(full_path)
        # FastAPI's path-converter accepts the URL as-is; we percent-encode
        # the colon and slashes in the scheme prefix to keep the path
        # parser happy across proxies.
        encoded = quote(repo_url, safe="/:-")
        url = f"{self.endpoint}{_GIMIE_PATH}/{encoded}"
        params = {"force_refresh": "true" if force_refresh else "false"}

        try:
            resp = self._client.get(url, params=params, headers=self._auth_headers())
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"metadata_extractor: HTTP error fetching {repo_url}: {exc}"
            ) from exc

        if resp.status_code != 200:
            raise RuntimeError(
                f"metadata_extractor: HTTP {resp.status_code} for {repo_url}: "
                f"{resp.text[:200]}"
            )
        body = resp.json()
        if not isinstance(body, dict):
            raise RuntimeError(
                f"metadata_extractor: response for {repo_url} is not a JSON object"
            )
        return body

    # -- v2 async path (rule_based by default; no LLM token needed) --------

    def submit_extract(
        self,
        source_url: str,
        *,
        agent_runtime: str = "rule_based",
        output_format: str = "jsonld",
        include_internal_fields: bool = False,
    ) -> str:
        """POST ``/v2/extract`` and return the ``job_id``.

        ``agent_runtime`` defaults to ``"rule_based"`` so the GME server
        doesn't need an LLM provider token (e.g. ``RCP_TOKEN``).

        ``include_internal_fields`` keeps GME-internal data (now exposed
        under the ``gme-internal:`` namespace) in the response — bios,
        locations, GitHub flags, etc.
        """
        url = self.endpoint + _V2_EXTRACT_PATH
        body: dict[str, Any] = {
            "source_url": _normalize_repo_url(source_url),
            "output_format": output_format,
            "agent_runtime": agent_runtime,
            "include_internal_fields": include_internal_fields,
        }
        try:
            resp = self._client.post(url, json=body, headers=self._auth_headers())
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"metadata_extractor v2: HTTP error submitting {source_url}: {exc}"
            ) from exc
        if resp.status_code != 202:
            raise RuntimeError(
                f"metadata_extractor v2: HTTP {resp.status_code} on submit "
                f"for {source_url} — {resp.text[:200]}"
            )
        payload = resp.json()
        if not isinstance(payload, dict) or "job_id" not in payload:
            raise RuntimeError(
                f"metadata_extractor v2: unexpected submit response: {payload!r}"
            )
        return str(payload["job_id"])

    def get_extract_job(self, job_id: str) -> dict[str, Any]:
        """GET ``/v2/jobs/{job_id}``. Returns the raw V2ExtractJob dict."""
        url = f"{self.endpoint}{_V2_JOB_PATH}/{job_id}"
        try:
            resp = self._client.get(url, headers=self._auth_headers())
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"metadata_extractor v2: HTTP error polling {job_id}: {exc}"
            ) from exc
        if resp.status_code != 200:
            raise RuntimeError(
                f"metadata_extractor v2: HTTP {resp.status_code} polling {job_id} "
                f"— {resp.text[:200]}"
            )
        body = resp.json()
        if not isinstance(body, dict):
            raise RuntimeError(
                f"metadata_extractor v2: job {job_id} response is not a JSON object"
            )
        return body

    def wait_for_extract(
        self,
        job_id: str,
        *,
        poll_interval: float = _DEFAULT_V2_POLL_INTERVAL,
        timeout: float = _DEFAULT_V2_TIMEOUT,
    ) -> dict[str, Any]:
        """Poll until job leaves ``pending`` / ``running``.

        Returns the final job dict on ``completed``. Raises
        :class:`ExtractJobFailedError` on ``failed`` and
        :class:`ExtractJobTimeoutError` on deadline.
        """
        deadline = time.monotonic() + timeout
        consecutive_failures = 0
        while True:
            try:
                job = self.get_extract_job(job_id)
            except RuntimeError as exc:
                # ``get_extract_job`` wraps httpx errors in RuntimeError. We
                # match the substring so we don't swallow legitimate API
                # errors (e.g. malformed responses) which use a different
                # message prefix.
                msg = str(exc)
                if "HTTP error polling" not in msg:
                    raise
                consecutive_failures += 1
                logger.warning(
                    "metadata_extractor: poll for job %s failed (%d/%d): %s",
                    job_id,
                    consecutive_failures,
                    _MAX_TRANSIENT_POLL_FAILURES,
                    msg,
                )
                if consecutive_failures >= _MAX_TRANSIENT_POLL_FAILURES:
                    raise
                if time.monotonic() >= deadline:
                    raise ExtractJobTimeoutError(
                        f"v2 extract job {job_id} did not complete within {timeout:.0f}s "
                        f"(last poll error: {exc})"
                    ) from exc
                time.sleep(poll_interval)
                continue

            consecutive_failures = 0
            state = str(job.get("status", "")).lower()
            if state == "completed":
                return job
            if state == "failed":
                err = job.get("error") or {}
                detail = err.get("detail") if isinstance(err, dict) else None
                raise ExtractJobFailedError(
                    f"v2 extract job {job_id} failed: {detail or 'no detail provided'}"
                )
            if time.monotonic() >= deadline:
                raise ExtractJobTimeoutError(
                    f"v2 extract job {job_id} did not complete within {timeout:.0f}s "
                    f"(last state: {state!r})"
                )
            time.sleep(poll_interval)

    def extract_repo_jsonld_v2(
        self,
        source_url: str,
        *,
        agent_runtime: str = "rule_based",
        include_internal_fields: bool = False,
        poll_interval: float = _DEFAULT_V2_POLL_INTERVAL,
        timeout: float = _DEFAULT_V2_TIMEOUT,
    ) -> dict[str, Any]:
        """One-shot helper: submit + poll + return the V2ExtractResponse.

        Returns the full job result body (containing ``output``,
        ``stats``, etc.). The JSON-LD payload is at ``result["output"]``.
        """
        job_id = self.submit_extract(
            source_url,
            agent_runtime=agent_runtime,
            output_format="jsonld",
            include_internal_fields=include_internal_fields,
        )
        logger.info(
            "metadata_extractor v2: submitted job %s for %s (runtime=%s)",
            job_id,
            source_url,
            agent_runtime,
        )
        final = self.wait_for_extract(
            job_id, poll_interval=poll_interval, timeout=timeout
        )
        result = final.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(
                f"metadata_extractor v2: job {job_id} completed without a result body"
            )
        return result

    # -- Lifecycle ---------------------------------------------------------

    def close(self) -> None:
        """Release the underlying HTTP client."""
        self._client.close()


def _normalize_repo_url(full_path: str) -> str:
    """Accept ``owner/repo`` or a full URL; return a full HTTPS GitHub URL."""
    s = full_path.strip().rstrip("/")
    if s.startswith("http://") or s.startswith("https://"):
        return s
    return f"https://github.com/{s}"
