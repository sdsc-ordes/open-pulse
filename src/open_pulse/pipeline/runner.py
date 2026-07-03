"""Quest pipeline runner with retry and logging.

Builds :class:`~open_pulse.tasks.FunctionTask` instances from the quest
config, wraps each with configurable retry logic, and delegates execution
to :func:`~open_pulse.orchestrator.run_sequential` for checkpoint/resume
support.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

import yaml

from open_pulse.orchestrator import run_sequential
from open_pulse.services.container import ServiceContainer
from open_pulse.tasks import FunctionTask

from .apply_grimoire_projects import run_apply_grimoire_projects
from .archive_outputs import run_archive_outputs
from .config import QuestFileConfig, RetryConfig, StepConfig
from .crawler import run_crawler
from .frontier_extend import run_frontier_extend
from .metadata_extractor import run_metadata_extractor
from .neo4j_upload import run_neo4j_upload
from .sparql_upload import run_sparql_upload

logger = logging.getLogger(__name__)

StepFunc = Callable[[dict[str, object]], None]

# Ordering matters: archive_outputs is last so it runs AFTER any step
# that writes into the directory it archives (typically the
# metadata_extractor + sparql_upload pair).
STEP_REGISTRY: dict[str, StepFunc] = {
    "crawler": run_crawler,
    "frontier_extend": run_frontier_extend,
    "neo4j_upload": run_neo4j_upload,
    "metadata_extractor": run_metadata_extractor,
    "sparql_upload": run_sparql_upload,
    "apply_grimoire_projects": run_apply_grimoire_projects,
    "archive_outputs": run_archive_outputs,
}

STEP_NAMES: tuple[str, ...] = tuple(STEP_REGISTRY)


# -- Config loading -----------------------------------------------------------


def load_config(path: Path) -> QuestFileConfig:
    """Load and validate a quest YAML config.

    Raises ``FileNotFoundError`` when *path* does not exist. A missing
    config almost always means a mistyped ``--config`` name (e.g.
    ``-c epfl-enac-full`` when the file is ``quest.epfl-enac-full.yml``);
    silently falling back to an empty default pipeline ran a no-op
    ``default-quest`` with no seeds and hid the mistake.
    """
    if not path.exists():
        raise FileNotFoundError(f"quest config not found: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return QuestFileConfig.model_validate(raw or {})


# -- Retry wrapper ------------------------------------------------------------


def _wrap_with_retry(
    func: StepFunc,
    step_name: str,
    retry: RetryConfig,
) -> StepFunc:
    """Return a wrapper that retries *func* according to *retry* policy."""

    def _retrying(context: dict[str, object]) -> None:
        last_exc: Exception | None = None
        for attempt in range(1, retry.max_attempts + 1):
            try:
                func(context)
                return
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Step %r failed (attempt %d/%d): %s",
                    step_name,
                    attempt,
                    retry.max_attempts,
                    exc,
                )
                if attempt < retry.max_attempts:
                    logger.info(
                        "Retrying %r in %.1fs …",
                        step_name,
                        retry.backoff_seconds,
                    )
                    time.sleep(retry.backoff_seconds)

        assert last_exc is not None
        raise last_exc

    return _retrying


# -- Step context wrapper -----------------------------------------------------


def _wrap_with_step_context(
    func: StepFunc,
    step_name: str,
    step_cfg: StepConfig,
) -> StepFunc:
    """Attach immutable step config data to task context before execution."""
    step_cfg_data = step_cfg.model_dump()

    def _with_context(context: dict[str, object]) -> None:
        context["step_name"] = step_name
        context["step_config"] = deepcopy(step_cfg_data)
        func(context)

    return _with_context


# -- Task builder -------------------------------------------------------------


def build_tasks(config: QuestFileConfig) -> tuple[FunctionTask, ...]:
    """Build the ordered task tuple from *config*, skipping disabled steps."""
    quest = config.quest
    step_configs: dict[str, StepConfig] = {
        "crawler": quest.steps.crawler,
        "frontier_extend": quest.steps.frontier_extend,
        "neo4j_upload": quest.steps.neo4j_upload,
        "metadata_extractor": quest.steps.metadata_extractor,
        "sparql_upload": quest.steps.sparql_upload,
        "apply_grimoire_projects": quest.steps.apply_grimoire_projects,
        "archive_outputs": quest.steps.archive_outputs,
    }

    tasks: list[FunctionTask] = []
    for name in STEP_NAMES:
        step_cfg = step_configs[name]
        if not step_cfg.enabled:
            logger.info("Step %r is disabled — skipping", name)
            continue

        func = STEP_REGISTRY[name]
        with_context = _wrap_with_step_context(func, name, step_cfg)
        wrapped = _wrap_with_retry(with_context, name, quest.retry)
        tasks.append(FunctionTask(name=name, func=wrapped))

    return tuple(tasks)


# -- Logging setup ------------------------------------------------------------


def _configure_logging(config: QuestFileConfig) -> None:
    """Set up Python logging based on the quest logging config."""
    log_cfg = config.quest.logging
    level = getattr(logging, log_cfg.level.upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler()]

    if log_cfg.file:
        log_path = Path(log_cfg.file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path))

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


# -- Public entry points ------------------------------------------------------


def run_pipeline(
    config_path: Path,
    *,
    resume: bool = False,
    checkpoint_dir: Path | None = None,
) -> tuple[str, ...]:
    """Load config, build tasks, and run the full pipeline.

    Parameters
    ----------
    config_path:
        Path to the quest YAML config file.
    resume:
        When *True*, skip steps already recorded in the checkpoint file.
    checkpoint_dir:
        Directory for checkpoint files.  Defaults to ``.quest-checkpoints``
        in the current working directory.
    """
    config = load_config(config_path)
    _configure_logging(config)

    quest = config.quest
    base = checkpoint_dir or Path(".quest-checkpoints")
    checkpoint = base / f"{quest.name}.json"

    tasks = build_tasks(config)
    services = ServiceContainer.from_quest_config(quest)

    logger.info(
        "Starting quest %r with %d step(s)%s",
        quest.name,
        len(tasks),
        " (resuming)" if resume else "",
    )
    try:
        completed = run_sequential(
            tasks,
            checkpoint,
            resume=resume,
            initial_context={"services": services},
        )
    finally:
        services.close_all()
    logger.info("Quest %r finished — %d step(s) completed", quest.name, len(completed))
    return completed


def run_single_step(
    config_path: Path,
    step_name: str,
) -> None:
    """Run a single pipeline step (no checkpoint)."""
    if step_name not in STEP_REGISTRY:
        raise ValueError(
            f"Unknown step: {step_name!r}. Available: {', '.join(STEP_NAMES)}"
        )

    config = load_config(config_path)
    _configure_logging(config)

    quest = config.quest
    step_configs: dict[str, StepConfig] = {
        "crawler": quest.steps.crawler,
        "frontier_extend": quest.steps.frontier_extend,
        "neo4j_upload": quest.steps.neo4j_upload,
        "metadata_extractor": quest.steps.metadata_extractor,
        "sparql_upload": quest.steps.sparql_upload,
        "apply_grimoire_projects": quest.steps.apply_grimoire_projects,
        "archive_outputs": quest.steps.archive_outputs,
    }
    services = ServiceContainer.from_quest_config(quest)
    func = STEP_REGISTRY[step_name]
    with_context = _wrap_with_step_context(func, step_name, step_configs[step_name])
    wrapped = _wrap_with_retry(with_context, step_name, quest.retry)
    task = FunctionTask(name=step_name, func=wrapped)

    logger.info("Running single step %r for quest %r", step_name, quest.name)
    try:
        task.run({"services": services})
    finally:
        services.close_all()
    logger.info("Step %r completed", step_name)
