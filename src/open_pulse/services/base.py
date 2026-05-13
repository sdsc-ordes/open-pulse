"""Protocols for external service integrations."""

from __future__ import annotations

from typing import Protocol


class Neo4jServiceProtocol(Protocol):
    """Contract for Neo4j service clients."""

    endpoint: str

    def upload(self, context: dict[str, object]) -> None:
        """Upload pipeline payload to Neo4j."""

    def check_bolt(self) -> tuple[bool, str]:
        """Probe Neo4j Bolt reachability."""

    def close(self) -> None:
        """Release service resources."""


class SparqlStoreServiceProtocol(Protocol):
    """Contract for SPARQL-store service clients."""

    endpoint: str

    def upload(self, context: dict[str, object]) -> None:
        """Upload pipeline payload to the SPARQL store."""

    def check_sparql(self) -> tuple[bool, str]:
        """Probe SPARQL endpoint reachability."""

    def close(self) -> None:
        """Release service resources."""
