"""Shared service configuration defaults and models."""

from __future__ import annotations

from pydantic import BaseModel, Field

DEFAULT_NEO4J_HTTP_ENDPOINT = "http://localhost:7474"
DEFAULT_NEO4J_BOLT_ENDPOINT = "bolt://localhost:7687"
DEFAULT_SPARQL_ENDPOINT = "http://localhost:7878"
DEFAULT_SPARQL_AUTH_ENV = "SPARQL_AUTH"
DEFAULT_GRIMOIRELAB_DB = "localhost:5432"
DEFAULT_CRAWLER_ENDPOINT = "http://localhost:8000"
DEFAULT_CRAWLER_API_TOKEN_ENV = "CRAWLER_API_TOKEN"
DEFAULT_NEO4J_AUTH_ENV = "NEO4J_AUTH"
DEFAULT_METADATA_EXTRACTOR_ENDPOINT = "http://localhost:1234"


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
