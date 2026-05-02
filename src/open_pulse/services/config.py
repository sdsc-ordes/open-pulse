"""Shared service configuration defaults and models."""

from __future__ import annotations

from pydantic import BaseModel, Field

DEFAULT_NEO4J_HTTP_ENDPOINT = "http://localhost:7474"
DEFAULT_NEO4J_BOLT_ENDPOINT = "bolt://localhost:7687"
DEFAULT_TENTRIS_SPARQL_ENDPOINT = "http://localhost:7502/sparql"
DEFAULT_GRIMOIRELAB_DB = "localhost:5432"
DEFAULT_CRAWLER_ENDPOINT = "http://localhost:8000"
DEFAULT_CRAWLER_API_TOKEN_ENV = "CRAWLER_API_TOKEN"


class Neo4jServiceConfig(BaseModel):
    """Connection settings for the Neo4j service."""

    endpoint: str = DEFAULT_NEO4J_BOLT_ENDPOINT


class TentrisServiceConfig(BaseModel):
    """Connection settings for the Tentris service."""

    endpoint: str = DEFAULT_TENTRIS_SPARQL_ENDPOINT


class CrawlerServiceConfig(BaseModel):
    """Connection settings for the Open Pulse Crawler API.

    The Bearer token itself is never serialized — only the *name* of the env
    var that holds it. Resolution happens at call time inside ``CrawlerService``.
    """

    endpoint: str = DEFAULT_CRAWLER_ENDPOINT
    api_token_env: str = DEFAULT_CRAWLER_API_TOKEN_ENV


class ServicesConfig(BaseModel):
    """Top-level service config block for quest runs."""

    neo4j: Neo4jServiceConfig = Field(default_factory=Neo4jServiceConfig)
    tentris: TentrisServiceConfig = Field(default_factory=TentrisServiceConfig)
    crawler: CrawlerServiceConfig = Field(default_factory=CrawlerServiceConfig)
