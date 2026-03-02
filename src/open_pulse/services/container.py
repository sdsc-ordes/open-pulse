"""Service container for run-scoped service lifecycles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from open_pulse.services.config import ServicesConfig
from open_pulse.services.neo4j import Neo4jService
from open_pulse.services.tentris import TentrisService


class QuestConfigLike(Protocol):
    """Minimal quest config shape needed by the service container."""

    services: ServicesConfig


@dataclass(slots=True)
class ServiceContainer:
    """Run-scoped service container shared across pipeline steps."""

    neo4j: Neo4jService
    tentris: TentrisService

    @classmethod
    def from_services_config(cls, services: ServicesConfig) -> "ServiceContainer":
        """Create a service container from the quest services config block."""
        return cls(
            neo4j=Neo4jService(endpoint=services.neo4j.endpoint),
            tentris=TentrisService(endpoint=services.tentris.endpoint),
        )

    @classmethod
    def from_quest_config(cls, quest: QuestConfigLike) -> "ServiceContainer":
        """Create a service container from full quest config."""
        return cls.from_services_config(quest.services)

    def close_all(self) -> None:
        """Close all managed services."""
        self.neo4j.close()
        self.tentris.close()

    def __enter__(self) -> "ServiceContainer":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close_all()
