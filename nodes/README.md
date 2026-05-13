# Open Pulse hosted instances

Each hosted Open Pulse instance lives as a single YAML file in this
directory. They are rendered on the project landing page
(`docs/index.html`) under **Try a hosted instance**.

## Add your node

The fastest path is the
[node builder](https://sdsc-ordes.github.io/open-pulse/node-builder/) —
a static, browser-only form that previews the YAML live and opens
GitHub's file editor with it pre-filled. No clone needed.

If you'd rather work from a clone:

1. Copy `epfl.yaml` to `<your-org>.yaml`.
2. Fill in the fields (see schema below).
3. Run `node scripts/build-nodes.mjs` from the repo root to regenerate
   `docs/data/nodes.json`.
4. Open a PR. Once merged, the docs-pages-deploy workflow publishes
   your node to the landing automatically (CI re-runs the build
   script before deploy, so a stale JSON is not fatal).

## Schema

| Field | Required | Notes |
| --- | --- | --- |
| `name` | yes | Short display name (e.g. `EPFL`). |
| `institution` | yes | Owning institution / programme. |
| `location` | yes | City, country. |
| `flag` | no | Single emoji shown when no `logo` is set. Defaults to 🌐. |
| `logo` | no | Relative path to a logo image (SVG/PNG) inside `docs/`, e.g. `./statics/Logo_EPFL_2019.svg`. Takes precedence over `flag`. |
| `url` | yes | Full URL the **Open** button points at. Must be `https://`. |
| `status` | yes | `live`, `beta`, or `coming-soon`. |
| `description` | yes | One- or two-sentence pitch. Multi-line block scalars are supported. |
| `contact` | no | Email or handle of the maintainer. |

Files are sorted alphabetically by filename; pick the filename
deliberately if you want a specific order.
