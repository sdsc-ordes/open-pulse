"""Shared service configuration defaults and models.

The default endpoints adapt to where the CLI is running:

* When the marker env var ``OPEN_PULSE_RUNNING_IN_CLI_CONTAINER=1`` is set
  (the case inside the ``open-pulse-cli`` container), defaults point at the
  compose-network service names (``crawler``, ``neo4j``, ``sparql-proxy``,
  ``extractor``). Quests written for in-container execution can therefore
  omit the ``services:`` block entirely.

* Otherwise, defaults point at ``localhost`` with the host-published ports.
  This is the right value for ``open-pulse health`` invoked directly on
  the host.

A quest YAML always wins: any explicit ``services.<svc>.endpoint`` value in
the file overrides these defaults.
"""

from __future__ import annotations

import os

from pydantic import BaseModel, Field

_IN_CLI_CONTAINER = os.environ.get("OPEN_PULSE_RUNNING_IN_CLI_CONTAINER") == "1"


def _default(in_cli: str, host: str) -> str:
    return in_cli if _IN_CLI_CONTAINER else host


DEFAULT_NEO4J_HTTP_ENDPOINT = _default("http://neo4j:7474", "http://localhost:7474")
DEFAULT_NEO4J_BOLT_ENDPOINT = _default("bolt://neo4j:7687", "bolt://localhost:7687")
DEFAULT_SPARQL_ENDPOINT = _default("http://sparql-proxy:7878", "http://localhost:7878")
DEFAULT_SPARQL_AUTH_ENV = "SPARQL_AUTH"
DEFAULT_GRIMOIRELAB_DB = _default("mariadb:3306", "localhost:5432")
DEFAULT_CRAWLER_ENDPOINT = _default("http://crawler:8000", "http://localhost:8000")
DEFAULT_CRAWLER_API_TOKEN_ENV = "CRAWLER_API_TOKEN"
DEFAULT_NEO4J_AUTH_ENV = "NEO4J_AUTH"
DEFAULT_METADATA_EXTRACTOR_ENDPOINT = _default(
    "http://extractor:1234", "http://localhost:1234"
)


class Neo4jServiceConfig(BaseModel):
    """Connection settings for the Neo4j service.

    Auth is resolved at call time from an env var (matching the value set on
    the Neo4j container, formatted as ``username/password``).
    """

    endpoint: str = DEFAULT_NEO4J_BOLT_ENDPOINT
    auth_env: str = DEFAULT_NEO4J_AUTH_ENV


class SparqlStoreServiceConfig(BaseModel):
    """Connection settings for the SPARQL store (technology-agnostic).

    ``endpoint`` is the **base URL** of the store, not a path-suffixed query
    URL: the service appends ``/store?default`` for uploads and ``/query``
    for reads itself. Auth (Basic Auth credentials for write endpoints) is
    resolved at call time from the env var named in ``auth_env``, formatted
    as ``username/password``.
    """

    endpoint: str = DEFAULT_SPARQL_ENDPOINT
    auth_env: str = DEFAULT_SPARQL_AUTH_ENV


class CrawlerServiceConfig(BaseModel):
    """Connection settings for the Open Pulse Crawler API.

    The Bearer token itself is never serialized — only the *name* of the env
    var that holds it. Resolution happens at call time inside ``CrawlerService``.
    """

    endpoint: str = DEFAULT_CRAWLER_ENDPOINT
    api_token_env: str = DEFAULT_CRAWLER_API_TOKEN_ENV


class MetadataExtractorServiceConfig(BaseModel):
    """Connection settings for the git-metadata-extractor (gimie) service.

    No client-side auth — the GME server reads ``GITHUB_TOKEN`` from its own
    environment. Open-pulse only needs the base URL.
    """

    endpoint: str = DEFAULT_METADATA_EXTRACTOR_ENDPOINT


class ServicesConfig(BaseModel):
    """Top-level service config block for quest runs."""

    neo4j: Neo4jServiceConfig = Field(default_factory=Neo4jServiceConfig)
    sparql_store: SparqlStoreServiceConfig = Field(
        default_factory=SparqlStoreServiceConfig
    )
    crawler: CrawlerServiceConfig = Field(default_factory=CrawlerServiceConfig)
    metadata_extractor: MetadataExtractorServiceConfig = Field(
        default_factory=MetadataExtractorServiceConfig
    )
