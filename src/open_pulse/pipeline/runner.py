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
from pathlib import Path

import yaml

from open_pulse.orchestrator import run_sequential
from open_pulse.services.container import ServiceContainer
from open_pulse.tasks import FunctionTask

from .config import QuestFileConfig, RetryConfig, StepConfig
from .crawler import run_crawler
from .metadata_extractor import run_metadata_extractor
from .neo4j_upload import run_neo4j_upload
from .tentris_upload import run_tentris_upload

logger = logging.getLogger(__name__)

StepFunc = Callable[[dict[str, object]], None]

STEP_REGISTRY: dict[str, StepFunc] = {
    "crawler": run_crawler,
    "neo4j_upload": run_neo4j_upload,
    "metadata_extractor": run_metadata_extractor,
    "tentris_upload": run_tentris_upload,
}

STEP_NAMES: tuple[str, ...] = tuple(STEP_REGISTRY)


# -- Config loading -----------------------------------------------------------


def load_config(path: Path) -> QuestFileConfig:
    """Load and validate a quest YAML config.

    Returns default configuration when *path* does not exist.
    """
    if not path.exists():
        logger.debug("Config file %s not found; using defaults", path)
        return QuestFileConfig()

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


# -- Task builder -------------------------------------------------------------


def build_tasks(config: QuestFileConfig) -> tuple[FunctionTask, ...]:
    """Build the ordered task tuple from *config*, skipping disabled steps."""
    quest = config.quest
    step_configs: dict[str, StepConfig] = {
        "crawler": quest.steps.crawler,
        "neo4j_upload": quest.steps.neo4j_upload,
        "metadata_extractor": quest.steps.metadata_extractor,
        "tentris_upload": quest.steps.tentris_upload,
    }

    tasks: list[FunctionTask] = []
    for name in STEP_NAMES:
        step_cfg = step_configs[name]
        if not step_cfg.enabled:
            logger.info("Step %r is disabled — skipping", name)
            continue

        func = STEP_REGISTRY[name]
        wrapped = _wrap_with_retry(func, name, quest.retry)
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
            f"Unknown step: {step_name!r}. "
            f"Available: {', '.join(STEP_NAMES)}"
        )

    config = load_config(config_path)
    _configure_logging(config)

    quest = config.quest
    services = ServiceContainer.from_quest_config(quest)
    func = STEP_REGISTRY[step_name]
    wrapped = _wrap_with_retry(func, step_name, quest.retry)
    task = FunctionTask(name=step_name, func=wrapped)

    logger.info("Running single step %r for quest %r", step_name, quest.name)
    try:
        task.run({"services": services})
    finally:
        services.close_all()
    logger.info("Step %r completed", step_name)
