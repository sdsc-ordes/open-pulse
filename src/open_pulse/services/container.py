"""Service container for run-scoped service lifecycles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from open_pulse.services.config import ServicesConfig
from open_pulse.services.crawler import CrawlerService
from open_pulse.services.metadata_extractor import MetadataExtractorService
from open_pulse.services.neo4j import Neo4jService
from open_pulse.services.sparql_store import SparqlStoreService


class QuestConfigLike(Protocol):
    """Minimal quest config shape needed by the service container."""

    services: ServicesConfig


@dataclass(slots=True)
class ServiceContainer:
    """Run-scoped service container shared across pipeline steps."""

    neo4j: Neo4jService
    sparql_store: SparqlStoreService
    crawler: CrawlerService
    metadata_extractor: MetadataExtractorService

    @classmethod
    def from_services_config(cls, services: ServicesConfig) -> "ServiceContainer":
        """Create a service container from the quest services config block."""
        return cls(
            neo4j=Neo4jService(
                endpoint=services.neo4j.endpoint,
                auth_env=services.neo4j.auth_env,
            ),
            sparql_store=SparqlStoreService(
                endpoint=services.sparql_store.endpoint,
                auth_env=services.sparql_store.auth_env,
            ),
            crawler=CrawlerService(
                endpoint=services.crawler.endpoint,
                api_token_env=services.crawler.api_token_env,
            ),
            metadata_extractor=MetadataExtractorService(
                endpoint=services.metadata_extractor.endpoint,
                api_token_env=services.metadata_extractor.api_token_env,
            ),
        )

    @classmethod
    def from_quest_config(cls, quest: QuestConfigLike) -> "ServiceContainer":
        """Create a service container from full quest config."""
        return cls.from_services_config(quest.services)

    def close_all(self) -> None:
        """Close all managed services."""
        self.neo4j.close()
        self.sparql_store.close()
        self.crawler.close()
        self.metadata_extractor.close()

    def __enter__(self) -> "ServiceContainer":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close_all()
