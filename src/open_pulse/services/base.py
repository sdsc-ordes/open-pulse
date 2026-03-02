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


class TentrisServiceProtocol(Protocol):
    """Contract for Tentris service clients."""

    endpoint: str

    def upload(self, context: dict[str, object]) -> None:
        """Upload pipeline payload to Tentris."""

    def check_sparql(self) -> tuple[bool, str]:
        """Probe Tentris SPARQL endpoint reachability."""

    def close(self) -> None:
        """Release service resources."""
