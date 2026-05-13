# Open Pulse hosted instances

Each hosted Open Pulse instance lives as a single YAML file in this
directory. They are rendered on the project landing page
(`docs/index.html`) under **Try a hosted instance**.

## Add your node

1. Copy `epfl.yaml` to `<your-org>.yaml`.
2. Fill in the fields (see schema below).
3. Run `node scripts/build-nodes.mjs` from the repo root to regenerate
   `docs/data/nodes.json`. CI verifies this is in sync; the build will
   fail otherwise.
4. Open a PR. Once merged, the docs-pages-deploy workflow publishes
   your node to the landing automatically.

## Schema

| Field | Required | Notes |
| --- | --- | --- |
| `name` | yes | Short display name (e.g. `EPFL`). |
| `institution` | yes | Owning institution / programme. |
| `location` | yes | City, country. |
| `flag` | no | Single emoji rendered in the card. Defaults to 🌐. |
| `url` | yes | Full URL the **Open** button points at. Must be `https://`. |
| `status` | yes | `live`, `beta`, or `coming-soon`. |
| `description` | yes | One- or two-sentence pitch. Block scalar (`|`) is supported for line breaks. |
| `contact` | no | Email or handle of the maintainer. |

Files are sorted alphabetically by filename; pick the filename
deliberately if you want a specific order.
