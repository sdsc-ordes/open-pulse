"""Tests for ``open_pulse.pipeline.archive_outputs``.

The step has no service dependencies — it's a pure filesystem operation
(walk a directory, write a zip, verify, optionally delete the source) —
so every test runs against a real ``tmp_path``.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from open_pulse.pipeline.archive_outputs import run_archive_outputs


def _make_jsonld_dir(root: Path, n: int) -> Path:
    """Create *n* fake JSON-LD files under ``root/metadata-json``."""
    d = root / "metadata-json"
    d.mkdir(parents=True)
    for i in range(n):
        (d / f"repo-{i:03d}.json").write_text(
            json.dumps({"@id": f"https://github.com/x/repo-{i}"}),
            encoding="utf-8",
        )
    return d


def _ctx(input_dir: Path, archive_dir: Path, **overrides: object) -> dict[str, object]:
    cfg: dict[str, object] = {
        "input_dir": str(input_dir),
        "archive_dir": str(archive_dir),
    }
    cfg.update(overrides)
    return {"step_config": cfg}


def test_archive_zips_dir_verifies_and_deletes_source(tmp_path: Path) -> None:
    source = _make_jsonld_dir(tmp_path, 5)
    archive_dir = tmp_path / "archives"

    run_archive_outputs(_ctx(source, archive_dir, archive_name="test-archive"))

    zip_path = archive_dir / "test-archive.zip"
    assert zip_path.is_file()
    assert not source.exists()  # source deleted on verify

    with zipfile.ZipFile(zip_path) as zf:
        names = sorted(n for n in zf.namelist() if not n.endswith("/"))
    # Every file lives under the source dir's basename inside the zip.
    assert names == [f"metadata-json/repo-{i:03d}.json" for i in range(5)]


def test_archive_keeps_source_when_delete_source_false(tmp_path: Path) -> None:
    source = _make_jsonld_dir(tmp_path, 3)
    archive_dir = tmp_path / "archives"

    run_archive_outputs(
        _ctx(source, archive_dir, archive_name="keep", delete_source=False)
    )

    assert (archive_dir / "keep.zip").is_file()
    assert source.exists()
    assert len(list(source.iterdir())) == 3  # untouched


def test_archive_no_source_dir_is_skip_not_failure(tmp_path: Path) -> None:
    archive_dir = tmp_path / "archives"
    # Source dir doesn't exist — step logs a warning and returns cleanly.
    run_archive_outputs(_ctx(tmp_path / "absent", archive_dir, archive_name="x"))

    # No archive file should have been written.
    assert not archive_dir.exists() or not any(archive_dir.iterdir())


def test_archive_empty_source_dir_is_skip(tmp_path: Path) -> None:
    source = tmp_path / "empty"
    source.mkdir()
    archive_dir = tmp_path / "archives"

    run_archive_outputs(_ctx(source, archive_dir, archive_name="empty"))

    assert source.exists()  # not deleted — we never wrote a zip to verify
    assert not (archive_dir / "empty.zip").exists()


def test_archive_default_name_includes_timestamp(tmp_path: Path) -> None:
    source = _make_jsonld_dir(tmp_path, 2)
    archive_dir = tmp_path / "archives"

    run_archive_outputs(_ctx(source, archive_dir))  # no archive_name override

    zips = sorted(archive_dir.glob("*.zip"))
    assert len(zips) == 1
    # Format: <source-dir-name>-YYYYMMDDHHMMSS.zip
    stem = zips[0].stem
    assert stem.startswith("metadata-json-")
    assert len(stem) == len("metadata-json-") + len("20260523074200")


def test_archive_rejects_file_as_input_dir(tmp_path: Path) -> None:
    # Pointing input_dir at a regular file should fail loud — silently
    # treating it as "empty dir" would mask a misconfigured quest.
    f = tmp_path / "not-a-dir.txt"
    f.write_text("hello", encoding="utf-8")
    archive_dir = tmp_path / "archives"

    with pytest.raises(RuntimeError, match="not a directory"):
        run_archive_outputs(_ctx(f, archive_dir, archive_name="x"))


def test_archive_step_registered_in_runner() -> None:
    """Smoke test: the runner imports and exposes archive_outputs in the
    canonical step registry. Without this, a quest with
    ``archive_outputs.enabled: true`` would silently no-op."""
    from open_pulse.pipeline.runner import STEP_NAMES, STEP_REGISTRY

    assert "archive_outputs" in STEP_REGISTRY
    # Should appear LAST so it runs after every step that writes to the
    # directory it archives.
    assert STEP_NAMES[-1] == "archive_outputs"


def test_archive_step_config_defaults() -> None:
    """The Pydantic step config has the right defaults: off, sensible
    paths, delete_source=True."""
    from open_pulse.pipeline.config import ArchiveOutputsStepConfig

    cfg = ArchiveOutputsStepConfig()
    assert cfg.enabled is False
    assert cfg.input_dir == ".quest-artifacts/metadata-json"
    assert cfg.archive_dir == "data/hub/archives"
    assert cfg.archive_name is None
    assert cfg.delete_source is True
