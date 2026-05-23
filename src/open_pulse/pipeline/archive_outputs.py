"""Archive-outputs pipeline step.

Zips the metadata_extractor output directory to a single .zip under
``data/hub/archives/`` and deletes the source on successful verify.
The hub Quests page lists + downloads from that archives directory.

Off by default. Enable it in a quest YAML when you want the per-quest
JSON-LD payloads packaged for download and the source dir freed.

Safety model:

1. Zip is written atomically: ``<final>.tmp`` first, then ``os.replace``
   to the final name.
2. After the zip is closed, we re-open it read-only, count files +
   ``testzip()``-style validate the CRC of every entry.
3. Only on a clean readback do we ``shutil.rmtree`` the source dir.
4. If anything fails — write error, count mismatch, CRC mismatch — we
   keep the source intact, drop any partial zip, and raise so the
   runner marks the step failed loud.
"""

from __future__ import annotations

import logging
import os
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _step_cfg(context: dict[str, object]) -> dict[str, object]:
    cfg = context.get("step_config", {})
    if not isinstance(cfg, dict):
        raise RuntimeError("Pipeline context 'step_config' must be a dict.")
    return cfg


def _file_count(path: Path) -> int:
    """Number of regular files anywhere under *path* (recursive)."""
    return sum(1 for p in path.rglob("*") if p.is_file())


def _zip_dir(source: Path, dest: Path) -> int:
    """Write *source* into *dest* as a flat-ish zip; return file count.

    Layout inside the zip: every regular file is stored at its path
    relative to ``source.parent``, so ``source = .../metadata-json-foo/``
    expands to ``metadata-json-foo/<files>``. This matches what the user
    expects when they extract the zip: a single top-level directory
    matching the original dir name.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    count = 0
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            arcname = path.relative_to(source.parent)
            zf.write(path, arcname=str(arcname))
            count += 1
    os.replace(tmp, dest)
    return count


def _verify_zip(path: Path, expected_count: int) -> None:
    """Open *path* read-only and assert it has *expected_count* entries
    and every entry's CRC matches. Raises on any mismatch."""
    with zipfile.ZipFile(path, "r") as zf:
        bad = zf.testzip()
        if bad is not None:
            raise RuntimeError(f"archive_outputs: CRC mismatch on {bad!r} in {path}")
        names = [n for n in zf.namelist() if not n.endswith("/")]
        if len(names) != expected_count:
            raise RuntimeError(
                f"archive_outputs: zip file count {len(names)} != source {expected_count}"
            )


def run_archive_outputs(context: dict[str, object]) -> None:
    """Zip the configured input directory, verify, and delete the source.

    Reads from ``step_cfg``:
      - ``input_dir`` (str): the directory to archive. Defaults to
        ``.quest-artifacts/metadata-json``. By convention this is the
        same path as ``metadata_extractor.output_dir``.
      - ``archive_dir`` (str): where the zip is written. Defaults to
        ``data/hub/archives`` so the hub can read it directly from its
        ``/data/hub`` mount.
      - ``archive_name`` (str | None): override the generated filename
        (without ``.zip``). When None, derived from the source dir name
        plus a ``YYYYMMDDHHMMSS`` UTC timestamp.
      - ``delete_source`` (bool): drop the source dir after a verified
        zip. Default True — the whole point of the step. Set False for
        a "copy out" pattern that keeps the source available.
    """
    cfg = _step_cfg(context)
    input_dir = Path(str(cfg.get("input_dir", ".quest-artifacts/metadata-json")))
    archive_dir = Path(str(cfg.get("archive_dir", "data/hub/archives")))
    delete_source = bool(cfg.get("delete_source", True))

    if not input_dir.exists():
        logger.warning(
            "archive_outputs: input_dir %s does not exist — skipping",
            input_dir,
        )
        return
    if not input_dir.is_dir():
        raise RuntimeError(
            f"archive_outputs: input_dir {input_dir} exists but is not a directory."
        )

    source_count = _file_count(input_dir)
    if source_count == 0:
        logger.warning(
            "archive_outputs: %s contains no files — skipping (nothing to archive)",
            input_dir,
        )
        return

    raw_name = cfg.get("archive_name")
    if raw_name:
        archive_name = str(raw_name)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        archive_name = f"{input_dir.name}-{ts}"
    dest = archive_dir / f"{archive_name}.zip"

    logger.info(
        "archive_outputs: zipping %d file(s) from %s → %s",
        source_count,
        input_dir,
        dest,
    )
    try:
        written = _zip_dir(input_dir, dest)
    except Exception:
        # Drop any partial .tmp the zipper may have left behind.
        for stray in archive_dir.glob(f"{archive_name}.zip.tmp"):
            try:
                stray.unlink()
            except OSError:
                pass
        raise

    if written != source_count:
        # _zip_dir already finished, so the dest exists — remove it and bail.
        try:
            dest.unlink()
        except OSError:
            pass
        raise RuntimeError(
            f"archive_outputs: wrote {written} files but source has {source_count}"
        )

    _verify_zip(dest, expected_count=source_count)

    if delete_source:
        shutil.rmtree(input_dir)
        logger.info(
            "archive_outputs: verified %s (%d files, %d bytes), removed source %s",
            dest,
            source_count,
            dest.stat().st_size,
            input_dir,
        )
    else:
        logger.info(
            "archive_outputs: verified %s (%d files, %d bytes), source kept",
            dest,
            source_count,
            dest.stat().st_size,
        )
