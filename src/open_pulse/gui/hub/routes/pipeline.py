"""Pipeline runner — list/read/create/run quest YAMLs inside the cli container.

We don't reimplement the quest runtime; we exec the CLI inside the
``open-pulse-cli`` container over the mounted docker socket. The hub becomes
the trigger surface; the orchestration logic stays in one place.
"""

from __future__ import annotations

import base64
import re
import shlex
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import docker
import yaml
from docker.errors import NotFound
from fastapi import APIRouter, Body, Depends, HTTPException, Query

import zipfile

from fastapi.responses import FileResponse

from ..auth import get_settings, require_auth, require_writable

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])

_CLI_CONTAINER = "open-pulse-cli"


def _client() -> docker.DockerClient:
    return docker.from_env()


def _cli_container() -> Any:
    try:
        return _client().containers.get(_CLI_CONTAINER)
    except NotFound:
        raise HTTPException(
            status_code=503,
            detail="open-pulse-cli container is not running. "
            "Bring it up with `--profile hub` (or `--with-cli`) first.",
        )


def _quests_dir(cli) -> str:
    """Absolute path of the quests directory inside the cli container.

    The cli container has the host repo bind-mounted at the same absolute
    path, so this also names the dir on the host. Quests live under
    ``data/quests/`` (gitignored, runtime config — not part of the Python
    package).
    """
    workspace = cli.attrs.get("Config", {}).get("WorkingDir") or "/workspace"
    return f"{workspace.rstrip('/')}/data/quests"


_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# Pipeline steps in their canonical order — matches StepsConfig in
# open_pulse.pipeline.config and the runner's STEP_REGISTRY. The hub uses
# this list to emit step pills in the UI even before the runner has logged
# anything for them.
PIPELINE_STEPS = (
    "crawler",
    "frontier_extend",
    "neo4j_upload",
    "metadata_extractor",
    "sparql_upload",
    "apply_grimoire_projects",
    "archive_outputs",
)


def _runs_dir() -> Path:
    """Where detached run logs land. Hub-side path (mounted from data/hub/)."""
    settings = get_settings()
    p = settings.data_dir / "runs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _slug_for_filename(value: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return s or "quest"


def _run_log_filename(quest_path: str, run_id: str) -> str:
    """Encode the quest basename in the filename so the list endpoint can
    find a quest's latest run by listing the runs dir."""
    base = Path(quest_path).stem  # strip .yml / .yaml
    return f"{_slug_for_filename(base)}__{run_id}.log"


# If a still-"running" log hasn't been touched in this many seconds we
# treat it as crashed/killed — the quest runner emits at least the per-poll
# crawler GET line every 5s while alive, so 5 minutes is generous.
_STALE_RUN_AFTER_SECONDS = 300.0

_RE_QUEST_START = re.compile(r"Starting quest '([^']+)' with (\d+) step")
_RE_STEP_DISABLED = re.compile(r"Step '([^']+)' is disabled")
_CRAWLER_JOB_RE = re.compile(r"submitted job_id=([0-9a-f-]{36})")
_RE_STEP_FAIL = re.compile(r"Step '([^']+)' failed \(attempt (\d+)/(\d+)\)")
_RE_QUEST_DONE = re.compile(r"Quest '([^']+)' finished")
# Final summary the orchestrator prints after a successful run.
_RE_PIPELINE_DONE = re.compile(
    r"Pipeline finished\. Completed \d+ step\(s\): ([^\n]+(?:\n\s*[^\n]+)*)"
)

# Per-step "I'm done" markers each step module emits in its own logger.
# The runner itself does NOT emit `Step 'X' completed` during `quest start`,
# so we infer step completion from these substrings instead.
_STEP_DONE_HINTS: dict[str, tuple[str, ...]] = {
    "crawler": ("crawler: wrote graph to ", "crawler: job "),
    "frontier_extend": (
        "frontier_extend: merged graph written to ",
        "frontier_extend: graph is closed",
        "frontier_extend: input graph",  # missing-input → step returned cleanly
    ),
    "neo4j_upload": ("neo4j_upload: ingested ",),
    "metadata_extractor": (
        "metadata_extractor: success=",
        "metadata_extractor: hit max_repos",
    ),
    "sparql_upload": ("sparql_upload: success=",),
    "apply_grimoire_projects": (
        "apply_grimoire_projects: applied ",
        "apply_grimoire_projects: no owners",
    ),
}


# -- Per-step stat extraction -------------------------------------------------
# Each step's modules log distinctive lines that the hub UI surfaces as
# little progress cards (mirrors the Crawler card). For long-running steps
# (metadata_extractor especially) we count partial events while the step is
# still in flight, then prefer the authoritative final-summary line once it
# lands.

_RE_NEO4J_DONE = re.compile(
    r"neo4j_upload: ingested users=(\d+) orgs=(\d+) repos=(\d+)"
)
_RE_METADATA_DONE = re.compile(
    r"metadata_extractor: success=(\d+) skipped=(\d+) failed=(\d+)"
)
_RE_METADATA_OK = re.compile(r"metadata_extractor \[\w+\]: (\S+) ->")
_RE_METADATA_FAIL = re.compile(r"metadata_extractor: (\S+) failed \(")
_RE_METADATA_SUBMIT = re.compile(
    r"metadata_extractor v\d+: submitted job \S+ for (\S+) "
)
_RE_SPARQL_DONE = re.compile(r"sparql_upload: success=(\d+) failed=(\d+) triples=(\d+)")
_RE_SPARQL_FAIL = re.compile(r"sparql_upload: (\S+) failed \(")
_RE_GRIMOIRE_DONE = re.compile(
    r"apply_grimoire_projects: applied (\d+) owners · (\d+) repos"
)
_RE_GRIMOIRE_NOOP = re.compile(r"apply_grimoire_projects: no owners matched")


def _parse_step_stats(text: str) -> dict[str, dict[str, Any]]:
    """Extract per-step progress counters from the runner log."""
    out: dict[str, dict[str, Any]] = {}

    m = _RE_NEO4J_DONE.search(text)
    if m:
        out["neo4j_upload"] = {
            "users": int(m.group(1)),
            "orgs": int(m.group(2)),
            "repos": int(m.group(3)),
            "final": True,
        }

    submitted = _RE_METADATA_SUBMIT.findall(text)
    successes = _RE_METADATA_OK.findall(text)
    failures = _RE_METADATA_FAIL.findall(text)
    m = _RE_METADATA_DONE.search(text)
    if m:
        out["metadata_extractor"] = {
            "success": int(m.group(1)),
            "skipped": int(m.group(2)),
            "failed": int(m.group(3)),
            "submitted": len(submitted),
            "final": True,
        }
    elif submitted or successes or failures:
        out["metadata_extractor"] = {
            "submitted": len(submitted),
            "success": len(successes),
            "failed": len(failures),
            "current": submitted[-1] if submitted else None,
            "final": False,
        }

    m = _RE_SPARQL_DONE.search(text)
    if m:
        out["sparql_upload"] = {
            "success": int(m.group(1)),
            "failed": int(m.group(2)),
            "triples": int(m.group(3)),
            "final": True,
        }
    else:
        sp_failed = _RE_SPARQL_FAIL.findall(text)
        if sp_failed:
            out["sparql_upload"] = {
                "failed": len(sp_failed),
                "final": False,
            }

    m = _RE_GRIMOIRE_DONE.search(text)
    if m:
        out["apply_grimoire_projects"] = {
            "owners": int(m.group(1)),
            "repos": int(m.group(2)),
            "applied": True,
            "final": True,
        }
    elif _RE_GRIMOIRE_NOOP.search(text):
        out["apply_grimoire_projects"] = {
            "owners": 0,
            "applied": False,
            "final": True,
        }

    return out


def _parse_run_log(text: str, *, age_seconds: float | None = None) -> dict[str, Any]:
    """Derive run state from the runner's + per-step modules' log markers.

    Returns ``{quest_name, statuses{step:'pending|running|done|skipped|failed'},
    finished, overall, current_step}``. ``overall`` is one of
    ``'pending' | 'running' | 'completed' | 'failed'`` — what the quest-list
    badge consumes.

    ``age_seconds`` is the seconds since the log file was last modified.
    When the log started but never wrote a terminating marker AND has
    been silent for longer than ``_STALE_RUN_AFTER_SECONDS``, we
    downgrade ``running`` to ``failed`` so the badge stops lying. Without
    this, a runner that was killed mid-flight (SIGKILL, container
    restart, host crash) would parade as "running" forever.
    """
    statuses = {s: "pending" for s in PIPELINE_STEPS}
    quest_name: str | None = None
    crawler_job_id: str | None = None
    finished = False
    started = False

    m = _RE_QUEST_START.search(text)
    if m:
        started = True
        quest_name = m.group(1)

    # The crawler step logs `submitted job_id=<uuid> seeds=N` once it gets
    # a 202 from the crawler API. The hub uses this to thread crawler-side
    # stats (queue depth, current round, ETA) into the run detail panel.
    m = _CRAWLER_JOB_RE.search(text)
    if m:
        crawler_job_id = m.group(1)

    for line in text.splitlines():
        m = _RE_STEP_DISABLED.search(line)
        if m and m.group(1) in statuses:
            statuses[m.group(1)] = "skipped"
            continue
        # Each step's own success summary marks it done.
        for step, hints in _STEP_DONE_HINTS.items():
            for h in hints:
                if h in line:
                    statuses[step] = "done"
        # Retry-exhausted failure (from the orchestrator).
        m = _RE_STEP_FAIL.search(line)
        if m and m.group(1) in statuses:
            attempt, total = int(m.group(2)), int(m.group(3))
            if attempt >= total:
                statuses[m.group(1)] = "failed"

    # The orchestrator's terminal summary is authoritative when present —
    # confirms which steps actually completed (handles edge cases where a
    # step's "done hint" message format drifts).
    m = _RE_PIPELINE_DONE.search(text)
    if m:
        completed = [s.strip() for s in m.group(1).replace("\n", " ").split(",")]
        for s in completed:
            if s in statuses and statuses[s] != "failed":
                statuses[s] = "done"
        finished = True
    if _RE_QUEST_DONE.search(text):
        finished = True

    # Mark the first not-yet-resolved step as 'running' while the quest is
    # in flight. After 'finished', any still-pending step stays pending
    # (e.g. quest aborted before reaching it).
    current_step: str | None = None
    if started and not finished:
        for s in PIPELINE_STEPS:
            if statuses[s] == "pending":
                statuses[s] = "running"
                current_step = s
                break

    if any(v == "failed" for v in statuses.values()):
        overall = "failed"
    elif finished or all(v in ("done", "skipped") for v in statuses.values()):
        overall = "completed"
    elif started:
        # Freshness check: a "running" log with no progress for several
        # minutes is almost certainly a dead runner (SIGKILL / container
        # restart / OOM). Downgrade to failed and pin the running step so
        # the UI shows where it died.
        if age_seconds is not None and age_seconds > _STALE_RUN_AFTER_SECONDS:
            overall = "failed"
            for s in PIPELINE_STEPS:
                if statuses[s] == "running":
                    statuses[s] = "failed"
                    break
        else:
            overall = "running"
    else:
        overall = "pending"

    return {
        "quest_name": quest_name,
        "statuses": statuses,
        "finished": finished,
        "overall": overall,
        "current_step": current_step,
        "crawler_job_id": crawler_job_id,
        "step_stats": _parse_step_stats(text),
    }


def _latest_run_for(quest_path: str) -> dict[str, Any] | None:
    """Return summary of the most-recent detached run for this quest, if any."""
    base = _slug_for_filename(Path(quest_path).stem)
    runs_dir = _runs_dir()
    candidates = sorted(
        runs_dir.glob(f"{base}__*.log"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return None
    log = candidates[0]
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    mtime = log.stat().st_mtime
    age_seconds = max(0.0, datetime.now(timezone.utc).timestamp() - mtime)
    parsed = _parse_run_log(text, age_seconds=age_seconds)
    return {
        "run_id": log.stem.split("__", 1)[-1],
        "log_filename": log.name,
        "started_at": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
        "overall": parsed["overall"],
        "statuses": parsed["statuses"],
        "current_step": parsed["current_step"],
    }


def _slugify(value: str) -> str:
    s = value.strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def _read_file_in_cli(cli, path: str) -> str:
    """Read a file from the cli container's filesystem via `cat`.

    The repo is bind-mounted at the host-identity path, so any path
    returned by ``list_quests`` is also a valid path inside the container.
    """
    rc, out = cli.exec_run(
        cmd=["cat", path],
        stdout=True,
        stderr=True,
        demux=False,
    )
    if rc not in (0, None):
        raise HTTPException(
            status_code=404,
            detail=(out or b"").decode("utf-8", "replace") or f"cannot read {path}",
        )
    return (out or b"").decode("utf-8", "replace")


def _summarize_yaml(text: str) -> dict[str, Any]:
    """Pull the human-readable bits out of a quest YAML.

    Returns ``{name, description, step_count, enabled_steps}``. Any parse
    failure yields ``{}`` — callers can still surface the raw text.
    """
    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return {}
    if not isinstance(doc, dict):
        return {}
    quest = doc.get("quest") or {}
    if not isinstance(quest, dict):
        return {}
    steps = quest.get("steps") or {}
    if not isinstance(steps, dict):
        steps = {}
    enabled: list[str] = []
    for step_name, step_cfg in steps.items():
        if isinstance(step_cfg, dict):
            if step_cfg.get("enabled", True):
                enabled.append(step_name)
        else:
            enabled.append(step_name)
    return {
        "name": str(quest.get("name") or "").strip() or None,
        "description": (str(quest.get("description") or "").strip() or None),
        "step_count": len(steps),
        "enabled_steps": enabled,
    }


@router.get("/quests", dependencies=[Depends(require_auth)])
def list_quests() -> dict[str, Any]:
    """List quest YAMLs with a one-line summary for each.

    Each entry has ``{path, name, summary, description, step_count,
    enabled_steps}``. The summary fields come from parsing the YAML; if
    parsing fails the path is still listed.
    """
    cli = _cli_container()

    quests_dir = _quests_dir(cli)
    rc, out = cli.exec_run(
        cmd=[
            "bash",
            "-lc",
            f"mkdir -p {shlex.quote(quests_dir)} >/dev/null 2>&1; "
            f"find {shlex.quote(quests_dir)} -maxdepth 1 "
            "\\( -name '*.yml' -o -name '*.yaml' \\) 2>/dev/null | sort",
        ],
        stdout=True,
        stderr=True,
        demux=False,
    )
    if rc not in (0, None):
        return {"quests": [], "error": (out or b"").decode("utf-8", "replace")}

    paths = [
        p for p in (out or b"").decode("utf-8", "replace").splitlines() if p.strip()
    ]
    quests: list[dict[str, Any]] = []
    for p in paths:
        entry: dict[str, Any] = {"path": p, "name": Path(p).name}
        try:
            text = _read_file_in_cli(cli, p)
            summary = _summarize_yaml(text)
            entry.update(
                {
                    "quest_name": summary.get("name"),
                    "description": summary.get("description"),
                    "step_count": summary.get("step_count"),
                    "enabled_steps": summary.get("enabled_steps") or [],
                }
            )
        except HTTPException:
            entry["error"] = "could not read"
        # Attach the latest detached-run summary so the list can show a
        # status badge per quest (running / completed / failed / pending).
        entry["last_run"] = _latest_run_for(p)
        quests.append(entry)
    return {"quests": quests}


@router.get("/quest", dependencies=[Depends(require_auth)])
def read_quest(
    path: str = Query(
        ..., description="Path of the quest YAML inside the cli container."
    ),
) -> dict[str, Any]:
    """Return the full text of a quest YAML plus its parsed summary.

    Restricted to ``*.yml`` / ``*.yaml`` to keep this endpoint from being a
    generic file-read tool. The file is read inside the cli container via
    ``cat`` over the docker socket.
    """
    if not path.endswith((".yml", ".yaml")):
        raise HTTPException(status_code=400, detail="path must end in .yml or .yaml")
    cli = _cli_container()
    text = _read_file_in_cli(cli, path)
    return {
        "path": path,
        "content": text,
        "summary": _summarize_yaml(text),
    }


@router.post("/run", dependencies=[Depends(require_auth), Depends(require_writable)])
def run_quest(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """Run a quest YAML inside the cli container and return its output."""
    path = (payload.get("path") or "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    if not path.endswith((".yml", ".yaml")):
        raise HTTPException(status_code=400, detail="path must end in .yml or .yaml")
    detach = bool(payload.get("detach", False))

    cli = _cli_container()
    base_cmd = ["open-pulse", "quest", "start", "--config", path]

    if detach:
        # Spawn the runner asynchronously and redirect its full output to a
        # known file under /data/hub/runs/. The cli compose mounts the same
        # OPEN_PULSE_DATA_DIR the hub uses at /data, so this path is
        # symmetric: hub reads it from /data/hub/runs (HUB_DATA_DIR=/data/hub),
        # cli writes it here. Decouples the cli's working dir from the
        # hub's data dir so OPEN_PULSE_DATA_DIR can be anywhere on disk.
        run_id = uuid.uuid4().hex[:12]
        log_dir = "/data/hub/runs"
        log_filename = _run_log_filename(path, run_id)
        log_path_in_cli = f"{log_dir}/{log_filename}"
        pid_path_in_cli = f"{log_dir}/{Path(log_filename).stem}.pid"

        # Run the quest in a subshell that:
        #   1. writes its own PID ($$) to a sibling .pid file
        #   2. exec's into open-pulse, replacing itself
        # Because exec keeps the PID, the .pid file IS the runner's PID and
        # the Stop endpoint can SIGTERM it. We grab $$ inside the subshell
        # rather than $! outside, because $! sometimes resolves to a parent
        # bash that's already gone by the time we want to kill.
        # NB: `$$` in bash returns the *invoking* shell's PID, not the
        # subshell's. We want the subshell's because `exec` keeps that
        # same PID for the open-pulse process. `$BASHPID` is the right
        # primitive (bash 4+).
        wrapper = (
            f"mkdir -p {shlex.quote(log_dir)} && "
            f"( echo $BASHPID > {shlex.quote(pid_path_in_cli)}; "
            f"  exec open-pulse quest start --config {shlex.quote(path)} "
            f"  > {shlex.quote(log_path_in_cli)} 2>&1 "
            f") & disown"
        )
        cli.exec_run(
            cmd=["bash", "-lc", wrapper],
            stdout=True,
            stderr=True,
            detach=True,
        )
        return {
            "detached": True,
            "run_id": run_id,
            "log_filename": log_filename,
            "command": base_cmd,
        }

    rc, out = cli.exec_run(
        cmd=base_cmd,
        stdout=True,
        stderr=True,
        demux=False,
        detach=False,
    )
    return {
        "exit_code": rc,
        "command": base_cmd,
        "output": (out or b"").decode("utf-8", "replace"),
    }


@router.get("/run-status", dependencies=[Depends(require_auth)])
def run_status(
    run_id: str = Query(
        ..., description="Run ID returned by POST /run with detach=true."
    ),
    tail: int = Query(80, description="How many lines of the log to return."),
) -> dict[str, Any]:
    """Tail the detached run's log + parse step statuses.

    Looks for a file under ``data/hub/runs/`` whose name ends in
    ``__{run_id}.log``. Returns the parsed step statuses, the overall
    status, and the last ``tail`` lines so the UI can show real progress
    instead of a frozen "Detached run started" message.
    """
    if tail <= 0 or tail > 5000:
        raise HTTPException(status_code=400, detail="tail must be in 1..5000")
    runs = _runs_dir()
    matches = sorted(runs.glob(f"*__{run_id}.log"))
    if not matches:
        return {
            "run_id": run_id,
            "exists": False,
            "overall": "pending",
            "statuses": {s: "pending" for s in PIPELINE_STEPS},
            "tail": "",
        }
    log = matches[0]
    text = log.read_text(encoding="utf-8", errors="replace")
    mtime = log.stat().st_mtime
    age_seconds = max(0.0, datetime.now(timezone.utc).timestamp() - mtime)
    parsed = _parse_run_log(text, age_seconds=age_seconds)
    lines = text.splitlines()
    tailed = "\n".join(lines[-tail:])
    return {
        "run_id": run_id,
        "exists": True,
        "log_filename": log.name,
        "started_at": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
        "size_bytes": log.stat().st_size,
        "line_count": len(lines),
        "age_seconds": age_seconds,
        "overall": parsed["overall"],
        "current_step": parsed["current_step"],
        "statuses": parsed["statuses"],
        "finished": parsed["finished"],
        "quest_name": parsed["quest_name"],
        "crawler_job_id": parsed["crawler_job_id"],
        "step_stats": parsed["step_stats"],
        "tail": tailed,
    }


@router.get("/runs", dependencies=[Depends(require_auth)])
def list_runs(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    """List the detached run logs sitting in ``data/hub/runs/``.

    Newest first. Each entry returns enough metadata for the hub to render
    a "recent runs" list without re-fetching every log body.
    """
    runs = _runs_dir()
    files = sorted(runs.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[dict[str, Any]] = []
    for p in files[:limit]:
        # Filename shape: <quest-base>__<run_id>.log
        stem = p.stem
        run_id = stem.rsplit("__", 1)[-1] if "__" in stem else stem
        quest_base = stem[: -(len(run_id) + 2)] if "__" in stem else stem
        st = p.stat()
        text = p.read_text(encoding="utf-8", errors="replace")
        age = max(0.0, datetime.now(timezone.utc).timestamp() - st.st_mtime)
        parsed = _parse_run_log(text, age_seconds=age)
        out.append(
            {
                "run_id": run_id,
                "log_filename": p.name,
                "quest_base": quest_base,
                "quest_name": parsed["quest_name"],
                "started_at": datetime.fromtimestamp(
                    st.st_mtime, tz=timezone.utc
                ).isoformat(),
                "size_bytes": st.st_size,
                "overall": parsed["overall"],
                "current_step": parsed["current_step"],
                "statuses": parsed["statuses"],
            }
        )
    return {"runs": out, "total": len(files)}


@router.get("/run-by-job", dependencies=[Depends(require_auth)])
def run_by_job(
    job_id: str = Query(..., description="Crawler job UUID to correlate."),
    tail: int = Query(80, ge=1, le=5000),
) -> dict[str, Any]:
    """Find the pipeline run whose log mentions the given crawler ``job_id``.

    Used by the Crawler-jobs drawer to surface the runner's full log
    (config validation, retries, downstream steps) next to the
    crawler-side status, since the crawler API itself doesn't ship logs.
    """
    if not re.fullmatch(r"[0-9a-fA-F-]{8,40}", job_id):
        raise HTTPException(status_code=400, detail="job_id has unexpected shape")
    runs = _runs_dir()
    files = sorted(runs.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files:
        text = p.read_text(encoding="utf-8", errors="replace")
        if job_id not in text:
            continue
        st = p.stat()
        age = max(0.0, datetime.now(timezone.utc).timestamp() - st.st_mtime)
        parsed = _parse_run_log(text, age_seconds=age)
        lines = text.splitlines()
        stem = p.stem
        run_id = stem.rsplit("__", 1)[-1] if "__" in stem else stem
        return {
            "found": True,
            "run_id": run_id,
            "log_filename": p.name,
            "started_at": datetime.fromtimestamp(
                st.st_mtime, tz=timezone.utc
            ).isoformat(),
            "size_bytes": st.st_size,
            "line_count": len(lines),
            "overall": parsed["overall"],
            "current_step": parsed["current_step"],
            "statuses": parsed["statuses"],
            "quest_name": parsed["quest_name"],
            "tail": "\n".join(lines[-tail:]),
        }
    return {"found": False, "job_id": job_id}


@router.get("/frontier-preview", dependencies=[Depends(require_auth)])
def frontier_preview(
    input_dir: str = Query(
        ".quest-artifacts/crawler-json",
        description="Directory holding the crawler graph (cli-container path).",
    ),
    input_filename: str = Query(
        "crawler-graph.json", description="Graph filename inside input_dir."
    ),
    sample: int = Query(20, ge=0, le=500),
) -> dict[str, Any]:
    """Compute the frontier of an existing crawler-graph without crawling.

    Mirrors :func:`open_pulse.pipeline.frontier_extend._compute_frontier`
    but runs hub-side over the cli container's filesystem. Lets the form
    show "you'd seed N repos" before launching, so the user isn't
    operating blind.
    """
    cli = _cli_container()
    workspace = cli.attrs.get("Config", {}).get("WorkingDir") or "/workspace"
    # Resolve relative inputs against the cli container's cwd. Strip a
    # leading "./" if present, but preserve a leading "." that's part of
    # the directory name (e.g. ".quest-artifacts").
    raw = input_dir.strip()
    if raw.startswith("./"):
        raw = raw[2:]
    if raw.startswith("/"):
        base = raw.rstrip("/")
    else:
        base = f"{workspace.rstrip('/')}/{raw.rstrip('/')}"
    path = f"{base}/{input_filename}"
    try:
        text = _read_file_in_cli(cli, path)
    except HTTPException:
        return {
            "path": path,
            "exists": False,
            "frontier_size": 0,
            "frontier_sample": [],
        }
    try:
        graph = yaml.safe_load(text)  # JSON is a subset of YAML; reuse loader
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"could not parse graph at {path}")
    if not isinstance(graph, dict):
        raise HTTPException(status_code=500, detail="graph payload is not an object")

    repos = graph.get("repos") or {}
    known = set(repos.keys())
    explored = {
        k for k, v in repos.items() if isinstance(v, dict) and v.get("is_explored")
    }
    mentioned: set[str] = set()
    for r in repos.values():
        if isinstance(r, dict):
            for tgt in r.get("dependents") or []:
                if isinstance(tgt, str) and tgt:
                    mentioned.add(tgt)
    frontier = sorted((mentioned - known) | ((mentioned & known) - explored))
    return {
        "path": path,
        "exists": True,
        "graph_repo_count": len(known),
        "graph_explored_count": len(explored),
        "dependent_edges_total": sum(
            len(r.get("dependents") or [])
            for r in repos.values()
            if isinstance(r, dict)
        ),
        "frontier_size": len(frontier),
        "frontier_sample": frontier[:sample] if sample > 0 else [],
    }


_VALID_STEPS = (
    "crawler",
    "frontier_extend",
    "neo4j_upload",
    "metadata_extractor",
    "sparql_upload",
    "apply_grimoire_projects",
)


def _build_quest_yaml(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Validate the create-quest payload and return (yaml_text, doc).

    The doc structure mirrors :class:`open_pulse.pipeline.config.QuestConfig`
    so a round-trip through the YAML loader is guaranteed to validate.
    """
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    slug = _slugify(name)
    if not _NAME_RE.match(slug):
        raise HTTPException(
            status_code=400,
            detail="name must slugify to [a-z0-9-]+ (1-64 chars, no leading dash)",
        )

    description = (payload.get("description") or "").strip() or None
    seeds_raw = payload.get("seeds") or []
    if isinstance(seeds_raw, str):
        seeds_raw = [s.strip() for s in seeds_raw.split(",")]
    seeds = [s for s in (str(x).strip() for x in seeds_raw) if s]

    enabled_steps = payload.get("steps") or {}
    if not isinstance(enabled_steps, dict):
        raise HTTPException(status_code=400, detail="steps must be an object")

    max_rounds = int(payload.get("max_rounds", 2) or 2)
    max_repos = int(payload.get("max_repos", 8) or 0)
    force_refresh = bool(payload.get("force_refresh", False))
    crawl_dependents = bool(payload.get("crawl_dependents", False))
    crawl_dependencies = bool(payload.get("crawl_dependencies", False))
    min_stars = max(0, int(payload.get("min_stars", 0) or 0))
    # max_dependents: 0 (or unset) means "no cap" → emit null in YAML so the
    # crawler keeps its default behaviour. Positive ints become a hard cap.
    md_raw = payload.get("max_dependents", 0) or 0
    max_dependents = int(md_raw) if int(md_raw) > 0 else None

    quest: dict[str, Any] = {"name": slug}
    if description:
        quest["description"] = description
    quest["retry"] = {
        "max_attempts": int(payload.get("max_attempts", 1) or 1),
        "backoff_seconds": int(payload.get("backoff_seconds", 0) or 0),
    }
    # NB: services block intentionally omitted — defaults pick compose-network
    # DNS when running inside the cli container.

    steps: dict[str, Any] = {}
    if enabled_steps.get("crawler", True):
        # timeout scales with depth: 1800s at max_rounds=2 was tight even for
        # ~370 repos and overflowed at max_rounds=3 (~2000 repos, ~43min).
        # Give each round its own budget instead of one flat ceiling.
        crawler_timeout = float(payload.get("crawler_timeout_seconds") or 0) or max(
            1800.0, 1800.0 * max_rounds
        )
        crawler_step: dict[str, Any] = {
            "seeds": seeds or ["sdsc-ordes"],
            "max_rounds": max_rounds,
            "crawl_dependencies": crawl_dependencies,
            "crawl_dependents": crawl_dependents,
            "min_stars": min_stars,
            "max_dependents": max_dependents,
            "poll_interval_seconds": 5.0,
            "timeout_seconds": crawler_timeout,
        }
        steps["crawler"] = crawler_step
    else:
        steps["crawler"] = {"enabled": False}

    # frontier_extend: reads the canonical graph and re-seeds the crawler
    # with repos that appear as dependents but were never explored. Mirrors
    # the crawler-graph knobs the user already configured.
    if enabled_steps.get("frontier_extend", False):
        fe_max_rounds = int(payload.get("frontier_max_rounds", 1) or 1)
        fe_cap_raw = payload.get("frontier_max_seeds", 0) or 0
        fe_cap = int(fe_cap_raw) if int(fe_cap_raw) > 0 else None
        fe_timeout = float(payload.get("frontier_timeout_seconds") or 0) or max(
            1800.0, 1800.0 * fe_max_rounds
        )
        frontier_step: dict[str, Any] = {
            "enabled": True,
            "max_rounds": fe_max_rounds,
            "crawl_dependencies": crawl_dependencies,
            "crawl_dependents": True,
            "min_stars": min_stars,
            "max_dependents": max_dependents,
            "max_frontier_seeds": fe_cap,
            "poll_interval_seconds": 5.0,
            "timeout_seconds": fe_timeout,
        }
        steps["frontier_extend"] = frontier_step
    else:
        steps["frontier_extend"] = {"enabled": False}
    steps["neo4j_upload"] = {"enabled": bool(enabled_steps.get("neo4j_upload", True))}
    if enabled_steps.get("metadata_extractor", True):
        steps["metadata_extractor"] = {
            "enabled": True,
            "max_repos": max_repos,
            "force_refresh": force_refresh,
        }
    else:
        steps["metadata_extractor"] = {"enabled": False}
    steps["sparql_upload"] = {"enabled": bool(enabled_steps.get("sparql_upload", True))}

    quest["steps"] = steps
    doc = {"quest": quest}
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False), doc


@router.post("/create", dependencies=[Depends(require_auth), Depends(require_writable)])
def create_quest(
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Create a quest YAML under ``data/quests/`` from form input.

    The file is written through the cli container (which has the repo
    bind-mounted at the same absolute path), so it lands on the host
    immediately and the next ``/api/pipeline/quests`` call surfaces it.

    Body::

        {
          "name": "human readable",
          "description": "...",
          "seeds": ["sdsc-ordes"],
          "max_rounds": 2, "max_repos": 8, "force_refresh": false,
          "max_attempts": 1, "backoff_seconds": 0,
          "steps": {"crawler": true, "neo4j_upload": true, ...},
          "overwrite": false        # default: 409 if file exists
        }

    Returns ``{path, content, summary, created}`` so the UI can immediately
    select the new quest.
    """
    yaml_text, _doc = _build_quest_yaml(payload)
    overwrite = bool(payload.get("overwrite", False))
    cli = _cli_container()
    quests_dir = _quests_dir(cli)
    name = _slugify(payload["name"])
    target = f"{quests_dir}/{name}.yml"

    # Check existence first so we can return a clean 409.
    rc, _ = cli.exec_run(cmd=["test", "-f", target], stdout=False, stderr=False)
    exists = rc == 0
    if exists and not overwrite:
        raise HTTPException(
            status_code=409,
            detail=f"quest already exists at {target}; pass overwrite=true to replace it.",
        )

    # Write via base64 so the YAML's quoting / special chars survive the
    # exec hop unchanged.
    encoded = base64.b64encode(yaml_text.encode("utf-8")).decode("ascii")
    write_cmd = (
        f"mkdir -p {shlex.quote(quests_dir)} && "
        f"echo {shlex.quote(encoded)} | base64 -d > {shlex.quote(target)}"
    )
    rc, out = cli.exec_run(cmd=["bash", "-lc", write_cmd], stdout=True, stderr=True)
    if rc not in (0, None):
        raise HTTPException(
            status_code=500,
            detail=f"write failed: {(out or b'').decode('utf-8', 'replace')[:300]}",
        )

    return {
        "path": target,
        "content": yaml_text,
        "summary": _summarize_yaml(yaml_text),
        "created": True,
        "overwritten": exists,
    }


@router.delete(
    "/quest", dependencies=[Depends(require_auth), Depends(require_writable)]
)
def delete_quest(
    path: str = Query(..., description="Absolute path of the quest YAML to delete."),
) -> dict[str, Any]:
    """Delete a user-created quest under ``data/quests/``.

    Refuses to touch anything outside that directory.
    """
    if not path.endswith((".yml", ".yaml")):
        raise HTTPException(status_code=400, detail="path must end in .yml or .yaml")
    cli = _cli_container()
    quests_dir = _quests_dir(cli)
    if not path.startswith(quests_dir + "/") or "/.." in path:
        raise HTTPException(
            status_code=400,
            detail=f"refusing to delete outside {quests_dir}",
        )
    rc, out = cli.exec_run(cmd=["rm", "-f", path], stdout=True, stderr=True)
    if rc not in (0, None):
        raise HTTPException(
            status_code=500,
            detail=(out or b"").decode("utf-8", "replace")[:300],
        )
    return {"path": path, "deleted": True}


@router.post(
    "/run-stop", dependencies=[Depends(require_auth), Depends(require_writable)]
)
def run_stop(
    run_id: str = Query(
        ..., description="Run ID returned by POST /run with detach=true."
    ),
    force: bool = Query(False, description="Use SIGKILL instead of SIGTERM."),
) -> dict[str, Any]:
    """Send SIGTERM (default) or SIGKILL (force=true) to the runner.

    Reads the PID file written at detach time, signals the process via
    ``bash -c "kill -SIG $PID"`` inside the cli container (the cli image
    doesn't ship a standalone ``kill`` binary, but bash's builtin works).
    A small grace period after TERM is the user's job — call again with
    ``force=true`` if the process refuses to exit.
    """
    runs = _runs_dir()
    matches = sorted(runs.glob(f"*__{run_id}.pid"))
    if not matches:
        raise HTTPException(
            status_code=404,
            detail=f"no PID file for run_id={run_id}; run may have started "
            f"before stop support, or is already gone.",
        )
    pid_file = matches[0]
    try:
        pid = pid_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not pid.isdigit():
        raise HTTPException(status_code=500, detail=f"unreadable pid: {pid!r}")

    sig = "KILL" if force else "TERM"
    cli = _cli_container()
    rc, out = cli.exec_run(
        cmd=["bash", "-lc", f"kill -{sig} {pid}"],
        stdout=True,
        stderr=True,
    )
    return {
        "run_id": run_id,
        "pid": int(pid),
        "signal": sig,
        "exit_code": rc,
        "output": (out or b"").decode("utf-8", "replace").strip(),
    }


# ── Archives ────────────────────────────────────────────────────────────────
#
# Zips produced by the ``archive_outputs`` pipeline step land in
# ``data/hub/archives/`` (resolved from the cli's CWD, which is bind-mounted
# at the same path the hub sees as ``/data/hub/archives``). The hub reads
# this directory directly — no docker-exec — to list and stream zips.


def _archives_dir() -> Path:
    """Where archive zips land on the hub side.

    The path is symmetric: ``archive_outputs`` writes to
    ``data/hub/archives`` relative to the cli's CWD; that lands at
    ``/data/hub/archives`` inside both containers via the same bind
    mount. Settings' ``data_dir`` resolves to ``/data/hub`` for the hub.
    """
    settings = get_settings()
    p = settings.data_dir / "archives"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_archive_name(name: str) -> str:
    """Sanitize a user-supplied archive filename.

    The download endpoint takes the name as a path segment; we treat it
    as a single basename (no directory traversal, no shell chars beyond
    the safe set). Refusing rather than rewriting so a malformed request
    returns 400, not a silently-different file.
    """
    if not name or "/" in name or "\\" in name or ".." in name or name.startswith("."):
        raise HTTPException(status_code=400, detail="invalid archive name")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise HTTPException(status_code=400, detail="invalid archive name")
    if not name.endswith(".zip"):
        raise HTTPException(status_code=400, detail="archive name must end in .zip")
    return name


@router.get("/archives", dependencies=[Depends(require_auth)])
def list_archives() -> dict[str, Any]:
    """List every ``.zip`` in the archives directory, newest first.

    Each entry: ``name``, ``size_bytes``, ``created_at`` (mtime as ISO),
    and ``file_count`` (cheap to read from the zip's central directory).
    Listed unfiltered; the Quests-page UI groups by quest-name prefix
    on the client side.
    """
    root = _archives_dir()
    files = sorted(root.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[dict[str, Any]] = []
    for p in files:
        st = p.stat()
        entry: dict[str, Any] = {
            "name": p.name,
            "size_bytes": st.st_size,
            "created_at": datetime.fromtimestamp(
                st.st_mtime, tz=timezone.utc
            ).isoformat(),
        }
        try:
            with zipfile.ZipFile(p, "r") as zf:
                entry["file_count"] = sum(
                    1 for n in zf.namelist() if not n.endswith("/")
                )
        except (zipfile.BadZipFile, OSError):
            entry["file_count"] = None  # corrupt or unreadable — still show it
        out.append(entry)
    return {"archives": out, "total": len(out)}


@router.get("/archives/{name}/download", dependencies=[Depends(require_auth)])
def download_archive(name: str) -> FileResponse:
    """Stream the named zip as a download.

    The browser sees a ``Content-Disposition: attachment`` so it offers
    a save dialog. Path traversal is blocked by ``_safe_archive_name``.
    """
    safe = _safe_archive_name(name)
    path = _archives_dir() / safe
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"archive {safe!r} not found")
    return FileResponse(
        path,
        media_type="application/zip",
        filename=safe,
    )


@router.delete(
    "/archives/{name}", dependencies=[Depends(require_auth), Depends(require_writable)]
)
def delete_archive(name: str) -> dict[str, Any]:
    """Remove an archive from the archives directory.

    Surfaced as a small action on the Quests-page list; the user
    keeps control over disk usage without shelling into the container.
    """
    safe = _safe_archive_name(name)
    path = _archives_dir() / safe
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"archive {safe!r} not found")
    size = path.stat().st_size
    path.unlink()
    return {"deleted": safe, "size_bytes": size}
