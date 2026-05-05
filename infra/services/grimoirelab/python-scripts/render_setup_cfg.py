"""Render Mordred's ``setup.cfg`` from the template + env, and copy projects.json.

Run inside the ``prepare-grimoire-config`` container before Mordred starts.
The container mounts:

    /workspace/templates/setup.cfg.template   ← config/setup.cfg.template (ro)
    /workspace/input/projects.json            ← config/projects.json      (ro)
    /workspace/output                         ← shared projects-conf dir   (rw)

We perform two operations:

1. Substitute ``${VAR}`` placeholders in the template using the container's
   environment (which is loaded from the project's ``.env`` via the compose
   ``env_file`` directive). The result is written to
   ``/workspace/output/setup.cfg`` — Mordred mounts the same directory at
   ``/home/grimoire/conf`` (read-only) and reads ``setup.cfg`` from there.

2. Copy ``projects.json`` to ``/workspace/output/projects.json`` so Mordred
   sees an up-to-date source-of-truth alongside the rendered config.

The substitution uses ``string.Template.safe_substitute`` so unknown
placeholders survive untouched (Mordred ignores empty values in many
sections, but a stray hard-stop on every typo would be more painful than
useful).
"""

from __future__ import annotations

import os
import shutil
import string
import sys
from pathlib import Path


# Composed values that aren't 1:1 env vars — built from the OpenSearch
# connection details. The template references ``${ES_COLLECTION_URL}`` and
# ``${ES_ENRICHMENT_URL}``; both point at the same OpenSearch instance with
# the admin credentials embedded so Mordred's elasticsearch-py client can
# connect without a separate auth config block.
def _build_es_url() -> str:
    base = os.environ.get("OPENSEARCH_URL", "https://opensearch-node1:9200")
    user = os.environ.get("OPENSEARCH_USERNAME", "")
    password = os.environ.get("OPENSEARCH_PASSWORD", "")
    if not user or not password:
        return base
    # https://user:pass@host:port — keep the scheme as-is.
    if "://" in base:
        scheme, rest = base.split("://", 1)
        return f"{scheme}://{user}:{password}@{rest}"
    return base


def _render() -> int:
    template_path = Path(
        os.environ.get(
            "SETUP_CFG_TEMPLATE_PATH",
            "/workspace/templates/setup.cfg.template",
        )
    )
    projects_src = Path(
        os.environ.get(
            "PROJECTS_JSON_PATH",
            "/workspace/input/projects.json",
        )
    )
    out_dir = Path(os.environ.get("RENDERED_CONFIG_DIR", "/workspace/output"))

    if not template_path.is_file():
        print(f"[render_setup_cfg] template not found: {template_path}", file=sys.stderr)
        return 2
    if not projects_src.is_file():
        print(f"[render_setup_cfg] projects.json not found: {projects_src}", file=sys.stderr)
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)

    # Build the substitution map: every env var the template might reference,
    # plus the composed ES URLs.
    es_url = _build_es_url()
    mapping: dict[str, str] = dict(os.environ)
    mapping.setdefault("ES_COLLECTION_URL", es_url)
    mapping.setdefault("ES_ENRICHMENT_URL", es_url)

    template_text = template_path.read_text(encoding="utf-8")
    rendered = string.Template(template_text).safe_substitute(mapping)

    setup_cfg = out_dir / "setup.cfg"
    setup_cfg.write_text(rendered, encoding="utf-8")
    print(f"[render_setup_cfg] wrote {setup_cfg} ({len(rendered)} bytes)", file=sys.stderr)

    projects_dst = out_dir / "projects.json"
    shutil.copyfile(projects_src, projects_dst)
    print(f"[render_setup_cfg] copied projects.json → {projects_dst}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(_render())
