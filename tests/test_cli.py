import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from open_pulse.cli import app
from open_pulse.commands import deploy as deploy_mod
from open_pulse.commands import grimoire as grimoire_mod
from open_pulse.commands import health as health_mod
from open_pulse.orchestrator import run_sequential
from open_pulse.pipeline.config import QuestFileConfig
from open_pulse.pipeline.runner import (
    STEP_NAMES,
    build_tasks,
    load_config,
    run_pipeline,
    run_single_step,
)
from open_pulse.tasks import FunctionTask

runner = CliRunner()


# -- Typer CLI tests ---------------------------------------------------------


def test_cli_help_exits_cleanly() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


# -- Deploy command tests ----------------------------------------------------


def test_deploy_up_no_docker_exits_1() -> None:
    with patch.object(deploy_mod, "_docker_available", return_value=False):
        result = runner.invoke(app, ["deploy", "up", "--profile", "default"])
    assert result.exit_code == 1
    assert "Docker" in result.output


def test_deploy_up_with_profile_flag(tmp_path: Path) -> None:
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("FOO=bar", encoding="utf-8")

    with (
        patch.object(deploy_mod, "_docker_available", return_value=True),
        patch.object(deploy_mod, "_find_project_root", return_value=tmp_path),
        patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
    ):
        result = runner.invoke(app, ["deploy", "up", "--profile", "analysis"])

    assert result.exit_code == 0
    cmd = mock_run.call_args[0][0]
    assert "--profile" in cmd
    assert "analysis" in cmd
    assert "up" in cmd
    assert "-d" in cmd


def test_deploy_up_creates_env_from_template(tmp_path: Path) -> None:
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}", encoding="utf-8")
    template_dir = tmp_path / "infra" / "env"
    template_dir.mkdir(parents=True)
    (template_dir / ".env.example").write_text("A=1\nB=2\n", encoding="utf-8")

    with (
        patch.object(deploy_mod, "_docker_available", return_value=True),
        patch.object(deploy_mod, "_find_project_root", return_value=tmp_path),
        patch("subprocess.run", return_value=MagicMock(returncode=0)),
    ):
        result = runner.invoke(app, ["deploy", "up", "--profile", "default"])

    assert result.exit_code == 0
    created = tmp_path / ".env"
    assert created.is_file()
    assert created.read_text(encoding="utf-8") == "A=1\nB=2\n"


def test_deploy_down_runs_compose_down(tmp_path: Path) -> None:
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}", encoding="utf-8")

    with (
        patch.object(deploy_mod, "_docker_available", return_value=True),
        patch.object(deploy_mod, "_find_project_root", return_value=tmp_path),
        patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
    ):
        result = runner.invoke(app, ["deploy", "down"])

    assert result.exit_code == 0
    cmd = mock_run.call_args[0][0]
    assert "down" in cmd


def test_deploy_ps_runs_compose_ps(tmp_path: Path) -> None:
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}", encoding="utf-8")

    with (
        patch.object(deploy_mod, "_docker_available", return_value=True),
        patch.object(deploy_mod, "_find_project_root", return_value=tmp_path),
        patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
    ):
        result = runner.invoke(app, ["deploy", "ps"])

    assert result.exit_code == 0
    cmd = mock_run.call_args[0][0]
    assert "ps" in cmd


# -- Quest config tests ------------------------------------------------------


def test_quest_config_defaults() -> None:
    cfg = QuestFileConfig()
    assert cfg.quest.name == "default-quest"
    assert cfg.quest.retry.max_attempts == 3
    assert cfg.quest.retry.backoff_seconds == 5.0
    assert cfg.quest.logging.level == "INFO"
    assert cfg.quest.logging.file is None
    assert cfg.quest.steps.crawler.enabled is True
    assert cfg.quest.steps.neo4j_upload.endpoint == "bolt://localhost:7687"
    assert cfg.quest.steps.tentris_upload.endpoint == "http://localhost:7502"


def test_quest_config_from_yaml(tmp_path: Path) -> None:
    data = {
        "quest": {
            "name": "test-run",
            "retry": {"max_attempts": 2, "backoff_seconds": 1},
            "steps": {
                "crawler": {"enabled": False},
                "neo4j_upload": {"endpoint": "bolt://db:7687"},
            },
        }
    }
    config_file = tmp_path / "quest.yml"
    config_file.write_text(yaml.dump(data), encoding="utf-8")

    cfg = load_config(config_file)
    assert cfg.quest.name == "test-run"
    assert cfg.quest.retry.max_attempts == 2
    assert cfg.quest.steps.crawler.enabled is False
    assert cfg.quest.steps.neo4j_upload.endpoint == "bolt://db:7687"
    assert cfg.quest.steps.metadata_extractor.enabled is True


def test_quest_config_missing_file_uses_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "nonexistent.yml")
    assert cfg.quest.name == "default-quest"
    assert cfg.quest.steps.crawler.enabled is True


# -- Quest pipeline unit tests -----------------------------------------------


def test_build_tasks_all_enabled() -> None:
    cfg = QuestFileConfig()
    tasks = build_tasks(cfg)
    assert [t.name for t in tasks] == list(STEP_NAMES)


def test_build_tasks_skips_disabled() -> None:
    cfg = QuestFileConfig()
    cfg.quest.steps.crawler.enabled = False
    cfg.quest.steps.tentris_upload.enabled = False

    tasks = build_tasks(cfg)
    names = [t.name for t in tasks]
    assert "crawler" not in names
    assert "tentris_upload" not in names
    assert "neo4j_upload" in names
    assert "metadata_extractor" in names


def test_pipeline_runs_all_steps(tmp_path: Path) -> None:
    completed = run_pipeline(
        tmp_path / "quest.yml",
        checkpoint_dir=tmp_path,
    )
    assert completed == ("crawler", "neo4j_upload", "metadata_extractor", "tentris_upload")


def test_pipeline_resumes_from_checkpoint(tmp_path: Path) -> None:
    checkpoint = tmp_path / "default-quest.json"
    checkpoint.write_text(
        json.dumps({"completed_tasks": ["crawler", "neo4j_upload"]}),
        encoding="utf-8",
    )

    completed = run_pipeline(
        tmp_path / "quest.yml",
        resume=True,
        checkpoint_dir=tmp_path,
    )
    assert completed == (
        "crawler",
        "neo4j_upload",
        "metadata_extractor",
        "tentris_upload",
    )


def test_pipeline_retry_on_transient_failure(tmp_path: Path) -> None:
    call_count = 0

    def flaky(_ctx: dict[str, object]) -> None:
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise RuntimeError("transient")

    data = {
        "quest": {
            "name": "retry-test",
            "retry": {"max_attempts": 3, "backoff_seconds": 0},
            "steps": {
                "crawler": {"enabled": False},
                "neo4j_upload": {"enabled": False},
                "metadata_extractor": {"enabled": False},
            },
        }
    }
    config_file = tmp_path / "quest.yml"
    config_file.write_text(yaml.dump(data), encoding="utf-8")

    with patch(
        "open_pulse.pipeline.runner.STEP_REGISTRY",
        {"crawler": flaky, "neo4j_upload": flaky, "metadata_extractor": flaky, "tentris_upload": flaky},
    ):
        completed = run_pipeline(config_file, checkpoint_dir=tmp_path)

    assert "tentris_upload" in completed
    assert call_count == 2


def test_single_step_runs_successfully(tmp_path: Path) -> None:
    run_single_step(tmp_path / "quest.yml", "crawler")


def test_single_step_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown step"):
        run_single_step(Path("quest.yml"), "nonexistent")


# -- Quest CLI command tests -------------------------------------------------


def test_quest_start_runs_pipeline() -> None:
    with patch(
        "open_pulse.commands.quest.run_pipeline",
        return_value=("crawler", "neo4j_upload", "metadata_extractor", "tentris_upload"),
    ):
        result = runner.invoke(app, ["quest", "start"])
    assert result.exit_code == 0
    assert "Pipeline finished" in result.output
    assert "4 step(s)" in result.output


def test_quest_run_step_executes() -> None:
    with patch("open_pulse.commands.quest.run_single_step"):
        result = runner.invoke(app, ["quest", "run-step", "crawler"])
    assert result.exit_code == 0
    assert "completed successfully" in result.output


def test_quest_run_step_unknown_errors() -> None:
    with patch(
        "open_pulse.commands.quest.run_single_step",
        side_effect=ValueError("Unknown step: 'foo'"),
    ):
        result = runner.invoke(app, ["quest", "run-step", "foo"])
    assert result.exit_code == 1
    assert "Unknown step" in result.output


def test_quest_list_steps() -> None:
    result = runner.invoke(app, ["quest", "list-steps"])
    assert result.exit_code == 0
    for name in STEP_NAMES:
        assert name in result.output


# -- Grimoire command tests --------------------------------------------------


def test_grimoire_prepare_config_writes_json(tmp_path: Path) -> None:
    output = tmp_path / "projects.json"
    result = runner.invoke(
        app,
        [
            "grimoire",
            "prepare-config",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0
    assert output.is_file()
    data = json.loads(output.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


def test_grimoire_prepare_config_custom_endpoints(tmp_path: Path) -> None:
    output = tmp_path / "out.json"
    result = runner.invoke(
        app,
        [
            "grimoire",
            "prepare-config",
            "--neo4j",
            "bolt://db:7687",
            "--tentris",
            "http://sparql:9000/sparql",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0
    assert "bolt://db:7687" in result.output
    assert "http://sparql:9000/sparql" in result.output


def test_grimoire_ui_missing_streamlit_exits_1() -> None:
    with patch.dict("sys.modules", {"streamlit": None}):
        with patch("builtins.__import__", side_effect=_import_without_streamlit):
            result = runner.invoke(app, ["grimoire", "ui"])
    assert result.exit_code == 1
    assert "Streamlit" in result.output


def _import_without_streamlit(name: str, *args: object, **kwargs: object) -> object:
    """Helper to simulate missing streamlit."""
    if name == "streamlit":
        raise ImportError("No module named 'streamlit'")
    return original_import(name, *args, **kwargs)


import builtins

original_import = builtins.__import__


def test_grimoire_ui_launches_streamlit() -> None:
    mock_streamlit = MagicMock()
    with (
        patch.dict("sys.modules", {"streamlit": mock_streamlit}),
        patch(
            "open_pulse.grimoire.streamlit_app.launch_streamlit",
        ) as mock_launch,
    ):
        result = runner.invoke(app, ["grimoire", "ui"])
    assert result.exit_code == 0
    mock_launch.assert_called_once()


def test_grimoire_install_watcher_requires_repo() -> None:
    result = runner.invoke(app, ["grimoire", "install-watcher"])
    assert result.exit_code != 0


def test_grimoire_install_watcher_calls_installer() -> None:
    with patch(
        "open_pulse.grimoire.cronjob.install_watcher",
    ) as mock_install:
        result = runner.invoke(
            app,
            [
                "grimoire",
                "install-watcher",
                "--repo",
                "https://github.com/org/repo.git",
                "--branch",
                "develop",
                "--schedule",
                "0 * * * *",
            ],
        )
    assert result.exit_code == 0
    mock_install.assert_called_once_with(
        repo_url="https://github.com/org/repo.git",
        config_path="projects.json",
        branch="develop",
        schedule="0 * * * *",
        clone_dir=None,
    )


# -- Grimoire unit tests (sparql_config) -------------------------------------


def test_sparql_generate_config_empty_repos(tmp_path: Path) -> None:
    from open_pulse.grimoire.sparql_config import generate_config

    out = generate_config(output=tmp_path / "projects.json")
    assert out.is_file()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data == {}


def test_sparql_build_projects_json() -> None:
    from open_pulse.grimoire.sparql_config import _build_projects_json

    repos = [
        {"name": "alpha", "repo": "https://github.com/org/alpha"},
        {"name": "beta", "repo": "https://github.com/org/beta"},
    ]
    result = _build_projects_json(repos)
    assert result == {
        "alpha": {"git": ["https://github.com/org/alpha"]},
        "beta": {"git": ["https://github.com/org/beta"]},
    }


# -- Grimoire unit tests (cronjob) ------------------------------------------


def test_cronjob_build_watcher_script() -> None:
    from open_pulse.grimoire.cronjob import _build_watcher_script

    script = _build_watcher_script(
        repo_url="https://github.com/org/repo.git",
        config_path="config.json",
        branch="main",
        clone_dir=Path("/tmp/watcher"),
    )
    assert "git clone" in script
    assert "config.json" in script
    assert "/tmp/watcher" in script


def test_cronjob_windows_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    from open_pulse.grimoire.cronjob import install_watcher

    monkeypatch.setattr("open_pulse.grimoire.cronjob.platform.system", lambda: "Windows")
    with pytest.raises(SystemExit):
        install_watcher(repo_url="https://github.com/org/repo.git")


# -- Health command tests -----------------------------------------------------


def test_health_no_docker_reports_unreachable() -> None:
    with patch.object(health_mod, "_docker_available", return_value=False):
        with patch.object(health_mod, "_probe_endpoints", return_value=[]):
            with patch.object(
                health_mod,
                "_smoke_tests",
                return_value=[("CLI version", True, "v0.1.0")],
            ):
                result = runner.invoke(app, ["health"])

    assert result.exit_code == 1
    assert "not" in result.output and "reachable" in result.output


def test_health_all_ok(tmp_path: Path) -> None:
    containers = [
        {
            "Name": "neo4j-open-pulse",
            "Service": "neo4j",
            "State": "running",
            "Status": "Up 5 minutes (healthy)",
            "Ports": "7474/tcp, 7687/tcp",
        }
    ]
    endpoints = [
        ("Neo4j (HTTP)", "http://localhost:7474", True, "HTTP 200"),
        ("Neo4j (Bolt)", "bolt://localhost:7687", True, "connection established"),
        ("Tentris (SPARQL)", "http://localhost:7502/sparql", True, "HTTP 200"),
        ("GrimoireLab DB", "localhost:5432", True, "connection established"),
    ]
    smoke = [
        ("CLI version", True, "v0.1.0"),
        ("Pipeline config schema", True, "default config validates"),
    ]

    with (
        patch.object(health_mod, "_docker_available", return_value=True),
        patch.object(health_mod, "_find_project_root", return_value=tmp_path),
        patch.object(health_mod, "_get_container_statuses", return_value=containers),
        patch.object(health_mod, "_probe_endpoints", return_value=endpoints),
        patch.object(health_mod, "_smoke_tests", return_value=smoke),
    ):
        result = runner.invoke(app, ["health"])

    assert result.exit_code == 0
    assert "All checks passed" in result.output


def test_health_failing_endpoint_exits_1(tmp_path: Path) -> None:
    endpoints = [
        ("Neo4j (HTTP)", "http://localhost:7474", False, "Connection refused"),
        ("Neo4j (Bolt)", "bolt://localhost:7687", True, "connection established"),
        ("Tentris (SPARQL)", "http://localhost:7502/sparql", True, "HTTP 200"),
        ("GrimoireLab DB", "localhost:5432", True, "connection established"),
    ]

    with (
        patch.object(health_mod, "_docker_available", return_value=True),
        patch.object(health_mod, "_find_project_root", return_value=tmp_path),
        patch.object(health_mod, "_get_container_statuses", return_value=[]),
        patch.object(health_mod, "_probe_endpoints", return_value=endpoints),
        patch.object(health_mod, "_smoke_tests", return_value=[]),
    ):
        result = runner.invoke(app, ["health"])

    assert result.exit_code == 1
    assert "Some checks failed" in result.output


def test_health_stopped_container_exits_1(tmp_path: Path) -> None:
    containers = [
        {
            "Name": "neo4j-open-pulse",
            "Service": "neo4j",
            "State": "exited",
            "Status": "Exited (1) 2 minutes ago",
            "Ports": "",
        }
    ]

    with (
        patch.object(health_mod, "_docker_available", return_value=True),
        patch.object(health_mod, "_find_project_root", return_value=tmp_path),
        patch.object(health_mod, "_get_container_statuses", return_value=containers),
        patch.object(health_mod, "_probe_endpoints", return_value=[]),
        patch.object(health_mod, "_smoke_tests", return_value=[]),
    ):
        result = runner.invoke(app, ["health"])

    assert result.exit_code == 1
    assert "exited" in result.output


def test_health_custom_endpoints() -> None:
    with (
        patch.object(health_mod, "_docker_available", return_value=False),
        patch.object(health_mod, "_probe_endpoints") as mock_probe,
        patch.object(health_mod, "_smoke_tests", return_value=[]),
    ):
        mock_probe.return_value = []
        runner.invoke(
            app,
            [
                "health",
                "--neo4j",
                "http://db:7474",
                "--neo4j-bolt",
                "bolt://db:7687",
                "--tentris",
                "http://sparql:9000/sparql",
                "--grimoirelab-db",
                "pghost:5433",
            ],
        )

    mock_probe.assert_called_once_with(
        "http://db:7474",
        "bolt://db:7687",
        "http://sparql:9000/sparql",
        "pghost:5433",
    )


def test_health_no_containers_shows_hint(tmp_path: Path) -> None:
    with (
        patch.object(health_mod, "_docker_available", return_value=True),
        patch.object(health_mod, "_find_project_root", return_value=tmp_path),
        patch.object(health_mod, "_get_container_statuses", return_value=[]),
        patch.object(health_mod, "_probe_endpoints", return_value=[]),
        patch.object(health_mod, "_smoke_tests", return_value=[]),
    ):
        result = runner.invoke(app, ["health"])

    assert "No containers found" in result.output


# -- Health unit tests -------------------------------------------------------


def test_probe_http_success() -> None:
    with patch("open_pulse.commands.health.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda self: self
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        ok, detail = health_mod._probe_http("http://localhost:7474")

    assert ok is True
    assert "200" in detail


def test_probe_http_unreachable() -> None:
    from urllib.error import URLError

    with patch(
        "open_pulse.commands.health.urlopen",
        side_effect=URLError("Connection refused"),
    ):
        ok, detail = health_mod._probe_http("http://localhost:9999")

    assert ok is False
    assert "refused" in detail.lower()


def test_probe_tcp_success() -> None:
    mock_sock = MagicMock()
    mock_sock.__enter__ = lambda self: self
    mock_sock.__exit__ = MagicMock(return_value=False)

    with patch("open_pulse.commands.health.socket.create_connection", return_value=mock_sock):
        ok, detail = health_mod._probe_tcp("localhost", 7687)

    assert ok is True
    assert "established" in detail


def test_probe_tcp_refused() -> None:
    with patch(
        "open_pulse.commands.health.socket.create_connection",
        side_effect=OSError("Connection refused"),
    ):
        ok, detail = health_mod._probe_tcp("localhost", 9999)

    assert ok is False
    assert "refused" in detail.lower()


def test_parse_host_port() -> None:
    assert health_mod._parse_host_port("myhost:1234", 5432) == ("myhost", 1234)
    assert health_mod._parse_host_port("myhost", 5432) == ("myhost", 5432)
    assert health_mod._parse_host_port("myhost:bad", 5432) == ("myhost:bad", 5432)


def test_smoke_tests_include_version() -> None:
    results = health_mod._smoke_tests(None, docker_ok=False)
    labels = [r[0] for r in results]
    assert "CLI version" in labels
    assert "Pipeline config schema" in labels

    for _label, passed, _detail in results:
        assert passed is True


def test_get_container_statuses_handles_empty(tmp_path: Path) -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        result = health_mod._get_container_statuses(tmp_path)
    assert result == []


def test_get_container_statuses_parses_json(tmp_path: Path) -> None:
    json_line = json.dumps(
        {"Name": "neo4j-open-pulse", "Service": "neo4j", "State": "running"}
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=json_line + "\n")
        result = health_mod._get_container_statuses(tmp_path)

    assert len(result) == 1
    assert result[0]["Name"] == "neo4j-open-pulse"


# -- Orchestrator tests (no CLI dependency) -----------------------------------


def test_orchestration_order_runs_in_sequence(tmp_path: Path) -> None:
    executed: list[str] = []

    def make_task(name: str) -> FunctionTask:
        return FunctionTask(name=name, func=lambda _ctx: executed.append(name))

    tasks = (make_task("one"), make_task("two"), make_task("three"))
    completed = run_sequential(tasks, tmp_path / "checkpoint.json")

    assert executed == ["one", "two", "three"]
    assert completed == ("one", "two", "three")


def test_failure_propagates(tmp_path: Path) -> None:
    from open_pulse.orchestrator import OrchestrationError

    def ok(_ctx: dict[str, object]) -> None:
        return None

    def fail(_ctx: dict[str, object]) -> None:
        raise RuntimeError("boom")

    tasks = (
        FunctionTask(name="ok-step", func=ok),
        FunctionTask(name="failing-step", func=fail),
    )

    with pytest.raises(OrchestrationError, match="failing-step"):
        run_sequential(tasks, tmp_path / "checkpoint.json")


def test_resume_continues_from_next_step(tmp_path: Path) -> None:
    executed: list[str] = []
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(
        json.dumps({"completed_tasks": ["first"]}),
        encoding="utf-8",
    )

    tasks = (
        FunctionTask(name="first", func=lambda _ctx: executed.append("first")),
        FunctionTask(name="second", func=lambda _ctx: executed.append("second")),
        FunctionTask(name="third", func=lambda _ctx: executed.append("third")),
    )
    completed = run_sequential(tasks, checkpoint, resume=True)

    assert executed == ["second", "third"]
    assert completed == ("first", "second", "third")


def test_shell_wrapper_forwards_args_and_exit_semantics() -> None:
    wrapper = Path(__file__).resolve().parents[1] / "tools" / "scripts" / "run-sequential.sh"
    content = wrapper.read_text(encoding="utf-8")

    assert "exec open-pulse run \"$@\"" in content
    assert "set -eu" in content
