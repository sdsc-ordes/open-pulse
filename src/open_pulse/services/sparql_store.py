"""SPARQL-store service client (technology-agnostic).

Speaks the SPARQL 1.1 Protocol + Graph Store HTTP Protocol — works against
any compliant store. The store-specific decision belongs to deploy/compose,
not this code.

- ``upload(jsonld_payload)``  →  ``POST  <endpoint>/store?default``
- ``check_sparql()``          →  ``GET   <endpoint>/``  (public, no auth)

Auth: Basic Auth credentials are resolved at call time from the env var
named in ``SparqlStoreServiceConfig.auth_env`` (default ``SPARQL_AUTH``),
formatted as ``username/password``. Reads don't need auth.

Format conversion: not all SPARQL stores accept ``application/ld+json``
directly via the Graph Store endpoint. We parse JSON-LD client-side with
rdflib and serialize to N-Triples, which is universally supported.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import httpx

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 5
_UPLOAD_TIMEOUT = 120.0
_DEFAULT_AUTH_ENV = "SPARQL_AUTH"
# Canonical base URI for auto-derived monthly snapshot graphs. Anything
# under this stem is owned by open-pulse; user-provided ``named_graph``
# overrides are otherwise unconstrained.
DEFAULT_GRAPH_BASE_URI = "https://open-pulse.epfl.ch/graph"


def derive_monthly_graph_uri(
    runtime: str,
    *,
    base_uri: str = DEFAULT_GRAPH_BASE_URI,
    now: dt.datetime | None = None,
) -> str:
    """Build the canonical named-graph URI for a monthly snapshot.

    The URI shape is ``{base_uri}/{YYYY-MM}/{runtime}`` — the date segment
    is an ISO 8601 year-month so snapshots sort lexically and are stable
    across centuries. Same month + same runtime → same URI, so multiple
    runs accumulate idempotently into one graph (entity URIs are global,
    so re-running over the same data is a set-merge no-op).

    Examples:
        derive_monthly_graph_uri("hybrid") →
            "https://open-pulse.epfl.ch/graph/2026-05/hybrid"
        derive_monthly_graph_uri("llm-inference") →
            "https://open-pulse.epfl.ch/graph/2026-05/llm-inference"

    ``now`` is injectable so tests can pin the month deterministically.
    """
    runtime_slug = (runtime or "").strip()
    if not runtime_slug:
        raise ValueError(
            "derive_monthly_graph_uri: runtime is required (e.g. 'hybrid')."
        )
    base = base_uri.rstrip("/")
    now = now or dt.datetime.now(dt.timezone.utc)
    return f"{base}/{now.strftime('%Y-%m')}/{runtime_slug}"


# Runtimes whose monthly snapshot we treat as the canonical "current
# view" — auto-publishing them to the default graph after a run. Anything
# else (rule_based, llm, llm-inference, …) stays in its named graph
# unless the quest explicitly opts in via ``publish_to_default: true``.
DEFAULT_RUNTIME_PUBLISHES_TO_DEFAULT = {"hybrid"}


class SparqlStoreService:
    """SPARQL-store HTTP client (technology-agnostic)."""

    def __init__(
        self,
        endpoint: str,
        auth_env: str = _DEFAULT_AUTH_ENV,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.auth_env = auth_env
        self._client: httpx.Client | None = None
        self._closed = False

    # -- Auth --------------------------------------------------------------

    def _resolved_auth(self) -> tuple[str, str] | None:
        """Return ``(user, password)`` if env is set; ``None`` otherwise.

        Reads are public on the proxy, so unset auth is non-fatal — only
        :meth:`upload` raises when credentials are missing.
        """
        raw = os.environ.get(self.auth_env, "")
        if not raw or "/" not in raw:
            return None
        user, password = raw.split("/", 1)
        return user, password

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=_UPLOAD_TIMEOUT)
        return self._client

    # -- Upload ------------------------------------------------------------

    def upload(
        self,
        jsonld_payload: dict[str, Any],
        *,
        named_graph: str | None = None,
    ) -> int:
        """Upload a JSON-LD payload to the SPARQL store.

        ``jsonld_payload`` may be either a top-level JSON-LD object/list or
        the wrapped gimie response (``{"output": [...], ...}``); we pull
        ``output`` out automatically when present.

        ``named_graph`` selects the target graph URI. When ``None`` the
        triples land in the default graph (``?default``).

        Returns the number of triples uploaded.
        """
        triples_payload = _normalize_jsonld(jsonld_payload)
        nt_bytes = _jsonld_to_ntriples(triples_payload)
        triple_count = nt_bytes.count(b"\n")
        if triple_count == 0:
            logger.info("sparql_store upload: 0 triples to upload, skipping")
            return 0

        auth = self._resolved_auth()
        if auth is None:
            raise RuntimeError(
                f"{self.auth_env} is not set or malformed; expected "
                "'username/password' for SPARQL writes."
            )

        url = f"{self.endpoint}/store"
        params = {"graph": named_graph} if named_graph else {"default": ""}
        headers = {"Content-Type": "application/n-triples"}

        resp = self._get_client().post(
            url,
            params=params,
            content=nt_bytes,
            headers=headers,
            auth=httpx.BasicAuth(*auth),
        )
        if resp.status_code not in (200, 201, 204):
            raise RuntimeError(
                f"sparql_store upload: HTTP {resp.status_code} on {url} — "
                f"{resp.text[:200]}"
            )
        logger.info(
            "sparql_store upload: posted %d triples to %s%s",
            triple_count,
            url,
            f" (graph={named_graph})" if named_graph else " (default)",
        )
        return triple_count

    # -- Snapshot publishing ----------------------------------------------

    def copy_to_default(self, source_graph: str) -> None:
        """Atomically replace the default graph with ``source_graph``.

        Uses SPARQL 1.1 Update's ``COPY <iri> TO DEFAULT`` — the store-side
        atomic swap that snapshot-promotion needs. The default graph is
        cleared and then refilled with every triple in ``source_graph``;
        ``source_graph`` itself is left untouched.

        Use case: keep ``default`` always pointing at the most-recent
        canonical snapshot (typically the latest hybrid extraction) so
        clients that don't specify a GRAPH still see the current view.
        Older monthly snapshots stay queryable via their named graph.
        """
        if not source_graph:
            raise ValueError("copy_to_default: source_graph is required")
        auth = self._resolved_auth()
        if auth is None:
            raise RuntimeError(
                f"{self.auth_env} is not set or malformed; expected "
                "'username/password' for SPARQL updates."
            )

        url = f"{self.endpoint}/update"
        update = f"COPY <{source_graph}> TO DEFAULT"
        resp = self._get_client().post(
            url,
            content=update.encode("utf-8"),
            headers={"Content-Type": "application/sparql-update"},
            auth=httpx.BasicAuth(*auth),
        )
        if resp.status_code not in (200, 201, 204):
            raise RuntimeError(
                f"sparql_store copy_to_default: HTTP {resp.status_code} on {url} — "
                f"{resp.text[:200]}"
            )
        logger.info("sparql_store copy_to_default: <%s> -> DEFAULT", source_graph)

    # -- Health probe ------------------------------------------------------

    def check_sparql(self) -> tuple[bool, str]:
        """Probe ``GET <endpoint>/`` (public). Returns ``(reachable, detail)``."""
        try:
            req = Request(self.endpoint + "/", method="GET")
            with urlopen(req, timeout=_CONNECT_TIMEOUT) as resp:  # noqa: S310
                return True, f"HTTP {resp.status}"
        except URLError as exc:
            return False, str(getattr(exc, "reason", exc))
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    # -- Lifecycle ---------------------------------------------------------

    def close(self) -> None:
        """Release the httpx client."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None
        self._closed = True


# -- JSON-LD helpers ----------------------------------------------------------


def _normalize_jsonld(payload: Any) -> Any:
    """Unwrap a metadata_extractor envelope into something rdflib can parse.

    Handles three input shapes (in priority order):

    1. **v2 framed** — ``{"output": {"@context": ..., "@graph": [...]}, ...}``:
       return the inner ``output`` object directly.
    2. **v1 expanded** — ``{"output": [<entity>, ...], ...}``: wrap the list
       under ``@graph`` so rdflib treats it as a single graph.
    3. **already-JSON-LD** — anything else: pass through unchanged.
    """
    if isinstance(payload, dict):
        output = payload.get("output")
        if isinstance(output, dict) and ("@graph" in output or "@context" in output):
            return output
        if isinstance(output, list):
            return {"@graph": output}
    return payload


def _jsonld_to_ntriples(payload: Any) -> bytes:
    """Parse a JSON-LD object with rdflib and serialize as N-Triples bytes."""
    from rdflib import Graph  # local import — keeps test imports fast

    g = Graph()
    g.parse(format="json-ld", data=json.dumps(payload))
    return g.serialize(format="nt", encoding="utf-8")
