"""Shared service configuration defaults and models."""

from __future__ import annotations

from pydantic import BaseModel, Field

DEFAULT_NEO4J_HTTP_ENDPOINT = "http://localhost:7474"
DEFAULT_NEO4J_BOLT_ENDPOINT = "bolt://localhost:7687"
DEFAULT_TENTRIS_SPARQL_ENDPOINT = "http://localhost:7502/sparql"
DEFAULT_GRIMOIRELAB_DB = "localhost:5432"


class Neo4jServiceConfig(BaseModel):
    """Connection settings for the Neo4j service."""

    endpoint: str = DEFAULT_NEO4J_BOLT_ENDPOINT


class TentrisServiceConfig(BaseModel):
    """Connection settings for the Tentris service."""

    endpoint: str = DEFAULT_TENTRIS_SPARQL_ENDPOINT


class ServicesConfig(BaseModel):
    """Top-level service config block for quest runs."""

    neo4j: Neo4jServiceConfig = Field(default_factory=Neo4jServiceConfig)
    tentris: TentrisServiceConfig = Field(default_factory=TentrisServiceConfig)
