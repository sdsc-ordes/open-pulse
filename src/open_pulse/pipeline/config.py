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
    fields are sent verbatim as the POST body to ``/api/v1/crawl`` (or
    ``/api/v1/crawl/graphql`` when ``use_graphql`` is true); only the
    trailing local-IO fields (``output_dir``, ``output_filename``) and the
    polling controls are read by the pipeline step itself.
    """

    seeds: list[str] = Field(default_factory=list)
    max_rounds: int = Field(default=2, ge=1, le=10)
    crawl_dependencies: bool = False
    crawl_dependents: bool = False
    min_stars: int = Field(default=0, ge=0)
    max_dependents: int | None = None
    max_contributors: int | None = None
    """Skip contributor expansion for repos with more than N contributors.
    The repo node stays in the graph; only its contributor users are not
    queued. Useful for avoiding mega-projects (Linux, k8s, …)."""
    # PR-8 opt-in flags — added to the crawler in
    # https://github.com/sdsc-ordes/open-pulse-crawler/pull/8. Default
    # False so existing quests don't trigger the (much heavier) issue
    # + PR scans by accident.
    crawl_issues: bool = False
    """Fetch issue authors and conversation commenters per repo (one
    GitHub API page per ``issue_max`` issues)."""
    crawl_prs: bool = False
    """Fetch PR authors, conversation commenters, and reviewers per repo
    (one GitHub API page per ``pr_max`` PRs)."""
    issue_max: int = Field(default=100, ge=1)
    pr_max: int = Field(default=100, ge=1)
    batch_size: int | None = None
    # GraphQL is the canonical endpoint per project convention — one
    # multi-resource query replaces N REST round-trips, so the GitHub
    # rate-limit cost is much lower for the same payload. Override to
    # False to fall back to the legacy REST endpoint at
    # ``/api/v1/crawl`` (e.g. to bisect a suspected GraphQL regression).
    use_graphql: bool = True
    output_dir: str = ".quest-artifacts/crawler-json"
    output_filename: str = "crawler-graph.json"
    poll_interval_seconds: float = 5.0
    timeout_seconds: float = 3600.0


class FrontierExtendStepConfig(StepConfig):
    """Frontier-extend step configuration.

    Reads ``input_dir/input_filename`` (the canonical crawler graph),
    computes the set of repos referenced as dependents but never explored,
    re-seeds the crawler with those, and merges the result back over the
    same path. Off by default — enable only when extending an existing
    graph is the goal of the quest.
    """

    enabled: bool = False
    input_dir: str = ".quest-artifacts/crawler-json"
    input_filename: str = "crawler-graph.json"
    output_dir: str | None = None  # None → write back to input_dir
    output_filename: str | None = None  # None → write back to input_filename
    max_rounds: int = Field(default=1, ge=1, le=10)
    crawl_dependencies: bool = False
    crawl_dependents: bool = True
    min_stars: int = Field(default=0, ge=0)
    max_dependents: int | None = None
    max_contributors: int | None = None
    crawl_issues: bool = False
    crawl_prs: bool = False
    issue_max: int = Field(default=100, ge=1)
    pr_max: int = Field(default=100, ge=1)
    batch_size: int | None = None
    max_frontier_seeds: int | None = None
    """Hard cap on how many frontier nodes to send as seeds. ``None`` means
    no cap. Useful when the frontier is huge and a smaller probe is
    desired."""
    # Matches ``CrawlerStepConfig.use_graphql`` so a frontier extension
    # uses the same endpoint convention as the parent crawl.
    use_graphql: bool = True
    poll_interval_seconds: float = 5.0
    timeout_seconds: float = 3600.0


class Neo4jUploadStepConfig(StepConfig):
    """Neo4j upload step configuration."""

    input_dir: str = ".quest-artifacts/crawler-json"
    input_filename: str = "crawler-graph.json"


class MetadataExtractorStepConfig(StepConfig):
    """Metadata extractor step configuration.

    The step reads the crawler graph, iterates each repo, asks the
    git-metadata-extractor for a JSON-LD payload, and writes one file
    per repo into ``output_dir``.

    ``mode`` selects the GME endpoint:
    - ``"v2"`` (default): async ``POST /v2/extract`` + poll. Honors
      ``v2_agent_runtime`` (``"rule_based"`` skips the LLM/RCP_TOKEN).
    - ``"v1_gimie"``: legacy synchronous ``GET /v1/repository/gimie/json-ld``.
    """

    input_dir: str = ".quest-artifacts/crawler-json"
    input_filename: str = "crawler-graph.json"
    output_dir: str = ".quest-artifacts/metadata-json"
    force_refresh: bool = False
    skip_existing: bool = True
    max_repos: int | None = None
    mode: str = "v2"
    v2_agent_runtime: str = "rule_based"
    v2_poll_interval_seconds: float = 2.0
    v2_timeout_seconds: float = 600.0
    max_workers: int = 6
    """Concurrent ``v2_extract`` submits the step runs at once. Matches the
    GME's ``V2_MAX_CONCURRENT_AGENTS`` default; raise/lower in the quest
    YAML when you've tuned the server. Set to ``1`` to revert to the old
    fully-sequential behaviour."""
    include_internal_fields: bool = False
    """Keep `_`-prefixed internal fields (``_bio``, ``_avatar_url``,
    ``_orcid_keywords``, ``_company``) in the response. The GME's strict
    SHACL validation still runs identically — this just controls what the
    consumer sees. Default False for ontology compliance."""


class SparqlUploadStepConfig(StepConfig):
    """SPARQL upload step configuration.

    Reads JSON-LD files written by the metadata_extractor step (one file
    per repo) and POSTs each to the SPARQL store's Graph Store endpoint.
    """

    input_dir: str = ".quest-artifacts/metadata-json"
    named_graph: str | None = None


class ApplyGrimoireProjectsStepConfig(StepConfig):
    """``apply_grimoire_projects`` step configuration.

    Builds an owner-grouped ``projects.json`` from the Neo4j graph and
    posts it to the GrimoireLab applier sidecar. Off by default so
    existing quests don't suddenly start writing to GrimoireLab.
    """

    enabled: bool = False
    include_unexplored: bool = False
    """Include Repo nodes the BFS discovered but didn't actually visit."""
    min_repos_per_owner: int = 1
    """Drop owners with fewer than this many repos. Default 1 keeps all."""
    title_prefix: str = ""
    """Prepended to each group's display title; useful for tagging the
    cohort (e.g. ``"c4dt: "``) when running multiple imports side-by-side."""
    applier_url: str = "http://projects-applier:8000"
    """Base URL of the projects-applier sidecar. The compose-network DNS
    is the right default; override for a remote GrimoireLab."""
    applier_auth_env: str = "APPLIER_AUTH"


class ArchiveOutputsStepConfig(StepConfig):
    """``archive_outputs`` step configuration.

    Zips a directory (by default ``metadata_extractor.output_dir``) into
    a single ``.zip`` under ``archive_dir``, verifies the zip's CRC and
    file count, then optionally deletes the source. Off by default so
    existing quests don't suddenly start consuming their own outputs.

    The default ``archive_dir`` lives under ``data/hub/archives/`` so
    the hub can list + serve the resulting zips directly from its
    ``/data/hub`` bind mount, without docker-exec.
    """

    enabled: bool = False
    input_dir: str = ".quest-artifacts/metadata-json"
    """Directory to archive. Match ``metadata_extractor.output_dir`` for
    the canonical "package up this quest's JSON-LD" flow."""
    archive_dir: str = "data/hub/archives"
    """Where the resulting ``.zip`` is written. Defaults under
    ``data/hub/`` because that path is mounted into the hub container at
    ``/data/hub/`` — the Quests page reads from there."""
    archive_name: str | None = None
    """Override the zip filename (without ``.zip``). When ``None``,
    derived from the input dir name plus a ``YYYYMMDDHHMMSS`` UTC
    timestamp, e.g. ``metadata-json-foo-20260523074200.zip``."""
    delete_source: bool = True
    """Drop the source directory once the zip is written and verified.
    The whole point of the step — set to ``False`` only for a "copy
    out" pattern where you also want the source kept."""


class StepsConfig(BaseModel):
    """Ordered collection of all pipeline step configs."""

    crawler: CrawlerStepConfig = Field(default_factory=CrawlerStepConfig)
    frontier_extend: FrontierExtendStepConfig = Field(
        default_factory=FrontierExtendStepConfig,
    )
    neo4j_upload: Neo4jUploadStepConfig = Field(default_factory=Neo4jUploadStepConfig)
    metadata_extractor: MetadataExtractorStepConfig = Field(
        default_factory=MetadataExtractorStepConfig,
    )
    sparql_upload: SparqlUploadStepConfig = Field(
        default_factory=SparqlUploadStepConfig,
    )
    apply_grimoire_projects: ApplyGrimoireProjectsStepConfig = Field(
        default_factory=ApplyGrimoireProjectsStepConfig,
    )
    archive_outputs: ArchiveOutputsStepConfig = Field(
        default_factory=ArchiveOutputsStepConfig,
    )


class QuestConfig(BaseModel):
    """Top-level quest configuration."""

    name: str = "default-quest"
    description: str | None = None
    """Free-form human-readable summary. Optional; surfaced by the hub
    Pipeline page next to the quest's name."""
    retry: RetryConfig = Field(default_factory=RetryConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    services: ServicesConfig = Field(default_factory=ServicesConfig)
    steps: StepsConfig = Field(default_factory=StepsConfig)


class QuestFileConfig(BaseModel):
    """Root model matching the on-disk YAML structure (``quest:`` key)."""

    quest: QuestConfig = Field(default_factory=QuestConfig)
