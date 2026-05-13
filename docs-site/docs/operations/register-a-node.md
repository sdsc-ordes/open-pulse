---
title: Register a hosted node
---

# Register a hosted node

Open Pulse can be self-hosted. To make your instance discoverable from
the project landing page, drop a YAML descriptor under `nodes/`. CI
regenerates `docs/data/nodes.json` and the landing automatically
renders your card on next deploy.

## Quick path — node builder

A static, browser-only form at
[`/node-builder/`](https://sdsc-ordes.github.io/open-pulse/node-builder/)
walks through the schema, previews the YAML live, and opens GitHub's
"new file" editor with the YAML pre-filled — no clone needed.

Workflow:

1. Open the [node builder](https://sdsc-ordes.github.io/open-pulse/node-builder/).
2. Fill in the form (name, URL, institution, …). The right-hand pane
   shows the YAML you'd commit.
3. Click **Open PR on GitHub** → lands you in the file editor at
   `github.com/sdsc-ordes/open-pulse/new/main/nodes/<slug>.yaml`.
4. Review, commit, open the PR. Maintainers merge it; the next
   docs-pages-deploy publishes your card.

## Manual path

If you'd rather work from a clone:

```bash
cp nodes/epfl.yaml nodes/<your-slug>.yaml
$EDITOR nodes/<your-slug>.yaml
node scripts/build-nodes.mjs   # regenerates docs/data/nodes.json
```

Open a PR with the new YAML (and the regenerated JSON, though CI will
also re-run the script before deploy).

## Schema

See [`nodes/README.md`](https://github.com/sdsc-ordes/open-pulse/blob/main/nodes/README.md)
for the field-by-field reference.

| Field | Required | Example |
| --- | --- | --- |
| `name` | yes | `EPFL` |
| `institution` | yes | `EPFL Open Science` |
| `location` | yes | `Lausanne, Switzerland` |
| `flag` | no | `🇨🇭` |
| `url` | yes | `https://openpulse.epfl.ch` |
| `status` | yes | `live` / `beta` / `coming-soon` |
| `description` | yes | One- or two-sentence pitch. Multi-line block scalars are supported. |
| `contact` | no | Email or handle of the maintainer. |
