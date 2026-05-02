"""Pydantic configuration models for the quest pipeline."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from open_pulse.services.config import ServicesConfig


class RetryConfig(BaseModel):
    """Retry behaviour applied to each pipeline step."""

    max_attempts: int = 3
    backoff_seconds: float = 5.0


class LoggingConfig(BaseModel):
    """Logging settings for a quest run."""

    level: str = "INFO"
    file: str | None = None


class StepConfig(BaseModel):
    """Base config shared by all pipeline steps."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)


class CrawlerStepConfig(StepConfig):
    """Crawler step configuration.

    Mirrors the ``CrawlRequest`` body of the Open Pulse Crawler API. These
    fields are sent verbatim as the POST body to ``/api/v1/crawl``; only
    the trailing local-IO fields (``output_dir``, ``output_filename``) and
    the polling controls are read by the pipeline step itself.
    """

    seeds: list[str] = Field(default_factory=list)
    max_rounds: int = Field(default=2, ge=1, le=10)
    crawl_dependencies: bool = False
    crawl_dependents: bool = False
    min_stars: int = Field(default=0, ge=0)
    max_dependents: int | None = None
    batch_size: int | None = None
    epfl_entities: list[str] = Field(default_factory=list)
    output_dir: str = ".quest-artifacts/crawler-json"
    output_filename: str = "crawler-graph.json"
    poll_interval_seconds: float = 5.0
    timeout_seconds: float = 3600.0


class Neo4jUploadStepConfig(StepConfig):
    """Neo4j upload step configuration."""

    input_dir: str = ".quest-artifacts/crawler-json"


class MetadataExtractorStepConfig(StepConfig):
    """Metadata extractor step configuration."""

    input_dir: str = ".quest-artifacts/crawler-json"
    output_dir: str = ".quest-artifacts/metadata-json"


class TentrisUploadStepConfig(StepConfig):
    """Tentris upload step configuration."""

    input_dir: str = ".quest-artifacts/metadata-json"


class StepsConfig(BaseModel):
    """Ordered collection of all pipeline step configs."""

    crawler: CrawlerStepConfig = Field(default_factory=CrawlerStepConfig)
    neo4j_upload: Neo4jUploadStepConfig = Field(default_factory=Neo4jUploadStepConfig)
    metadata_extractor: MetadataExtractorStepConfig = Field(
        default_factory=MetadataExtractorStepConfig,
    )
    tentris_upload: TentrisUploadStepConfig = Field(
        default_factory=TentrisUploadStepConfig,
    )


class QuestConfig(BaseModel):
    """Top-level quest configuration."""

    name: str = "default-quest"
    retry: RetryConfig = Field(default_factory=RetryConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    services: ServicesConfig = Field(default_factory=ServicesConfig)
    steps: StepsConfig = Field(default_factory=StepsConfig)


class QuestFileConfig(BaseModel):
    """Root model matching the on-disk YAML structure (``quest:`` key)."""

    quest: QuestConfig = Field(default_factory=QuestConfig)
