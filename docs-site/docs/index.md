---
title: Open Pulse Documentation
slug: /
---

import Tabs from '@theme/Tabs';
import TabItem from '@theme/TabItem';

# Open Pulse Documentation

Monitor the health of your open-source ecosystem — a unified CLI,
orchestrator, and FastAPI hub over a crawler → graph → SPARQL pipeline.
This site is the source of truth for Open Pulse docs.

## Get started

Pick the variant that fits your case. The EPFL-hosted instance is the
fastest path; no install needed.

<Tabs groupId="install" queryString defaultValue="epfl">
  <TabItem value="epfl" label="🟢 EPFL (preferred)">

Open Pulse runs as a managed instance at EPFL. No install required.

```text
https://openpulse.epfl.ch
```

Request access via the EPFL Open Source channel.

  </TabItem>
  <TabItem value="docker" label="🐳 Docker">

Pull the latest image from GHCR:

```bash
docker pull ghcr.io/sdsc-ordes/open-pulse:v1.0.0
```

Then bring up the stack with [`op deploy`](./getting-started/index.md#bring-up-the-stack).

  </TabItem>
  <TabItem value="pip" label="🐍 pip">

Install from PyPI:

```bash
pip install open-pulse-science          # core CLI
pip install 'open-pulse-science[hub]'   # + hub dashboard
```

The Python module remains `open_pulse`; the CLI entry point is `open-pulse`.

  </TabItem>
  <TabItem value="source" label="🛠 Source">

Clone and build from source:

```bash
git clone https://github.com/sdsc-ordes/open-pulse
cd open-pulse
docker build -f tools/images/Dockerfile-open-pulse -t open-pulse:local .
echo "OPEN_PULSE_IMAGE=open-pulse:local" >> infra/.env
./scripts/op deploy up --profile hub
```

  </TabItem>
</Tabs>

Continue with the [full Getting Started guide →](./getting-started/index.md)

## What changed recently

- The SPARQL store client was renamed from `open_pulse.services.tentris`
  to `open_pulse.services.sparql_store`. It is now technology-agnostic:
  any SPARQL 1.1 + Graph Store HTTP Protocol store works (Oxigraph,
  Tentris, Virtuoso, …). Quest YAML uses `quest.services.sparql_store.endpoint`;
  step-level `endpoint` fields are no longer supported.
- The Open Pulse stack lives entirely under `infra/open-pulse-stack/`:
  `docker-compose.yml` (main), `docker-compose.cli.yml` (CLI orchestrator
  overlay), `docker-compose.grimoirelab.yml` (full GrimoireLab — opt-in
  via `--with-grimoire`), plus GrimoireLab supporting assets.
- One Docker image (`tools/images/Dockerfile-open-pulse` →
  `ghcr.io/sdsc-ordes/open-pulse:latest`) plays three roles via compose
  overrides: host install, `open-pulse-cli` (idle, target of `docker exec`
  from `scripts/op`), and `open-pulse-hub` (FastAPI dashboard).
- Single-file env model: `infra/.env` is the only file Docker Compose
  loads. Every service `env_file:`-pulls it. `<repo>/.env` is for the
  open-pulse Python CLI / hub when running on the host against EXTERNAL
  infrastructure; compose never reads it.
- The pipeline gained an optional `apply_grimoire_projects` step that
  builds an owner-grouped `projects.json` from Neo4j and POSTs it to the
  GrimoireLab applier sidecar. Off by default.
- The hub default port moved from 9090 to 7507 on EPFL hosts to land
  inside the firewall-open range. `HUB_PORT` in `infra/.env` controls it.
- Default auth simplified to `openpulse` / `replace-me` (rotate before
  any non-local deployment). OpenSearch needs a stronger placeholder
  (`Replace-Me-1!`) to satisfy its security plugin's regex.

## Start here

- [Getting Started](./getting-started/index.md)
- [Architecture](./architecture/index.md)
- [Services](./services/index.md)
- [Analysis](./analysis/index.md)
- [Operations](./operations/index.md)
