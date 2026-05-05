# Follow-ups after the hub / single-image refactor

Captured 2026-05-04 at the end of the hub-design + image-unification session.
Each entry has a one-line description, the **Why**, and the **How to apply**
(short, so the next agent / contributor can pick it up cold).

---

## High-impact

### 1. Publish `ghcr.io/sdsc-ordes/open-pulse/open-pulse:*` from CI

**Why:** the compose files default to that image tag, but no release workflow
currently publishes it. Anyone bringing the stack up must build locally and
override `OPEN_PULSE_IMAGE` in `.env` first.

**How to apply:** add a GitHub Actions workflow that on tag push (and ideally
on `develop` push for `:edge`) builds `tools/images/Dockerfile-open-pulse`
and pushes to GHCR. Probably extends the existing
`.github/workflows/release.yml` and `.github/workflows/docker-validate.yml`.
Sister repo `open-pulse-crawler` already has a publish workflow — start from
there.

---

### 2. Regenerate `uv.lock` to include the `[hub]` extra

**Why:** the Dockerfile currently runs `uv sync … --extra hub` **without**
`--frozen` because the committed `uv.lock` predates the extra. That makes
the build effectively-frozen on a per-build basis (uv resolves fresh each
time) but loses lock-driven reproducibility and re-fetches metadata.

**How to apply:** on a host with `uv` available, run `uv lock` from the repo
root. Commit the updated `uv.lock`. Then re-add `--frozen` to the
`uv sync` call in `tools/images/Dockerfile-open-pulse`. Verify by rebuilding
and checking `docker run --rm open-pulse:local --version` still works.

---

### 3. Pull the projects-applier sidecar into the main compose

**Why:** the hub's default `HUB_APPLIER_URL=http://projects-applier:8000`
only resolves when the grimoirelab stack is up. In the most common dev
flow (main stack with hub, grimoire optional), the **Projects** page's
"Apply" button breaks because the applier isn't on the network.

**How to apply:** copy the applier service definition from
`infra/services/grimoirelab/docker-compose.yml` into
`infra/compose/docker-compose.yml` under a new `projects-applier` profile.
Keep the grimoirelab compose's copy too (or have grimoirelab depend on the
main applier — cleaner). Update `infra/services/grimoirelab/applier/main.py`
to handle the case where the mordred container isn't running (skip the
restart silently).

---

## Medium

### 4. Pipeline page: synchronous quest run hits the docker exec ceiling

**Why:** `/api/pipeline/run` calls `docker exec open-pulse-cli open-pulse
quest run <yaml>` and waits for output. Long quests (more than ~10 min) get
truncated. The page's "detach" toggle is a workaround but loses output.

**How to apply:** move quest runs to a background job pattern. Options:
(a) write run output to `data/hub/runs/<run-id>.log` and stream to the UI
via SSE; (b) front-end polls `/api/pipeline/runs/<id>` for status. The
docker SDK's `exec_run(..., stream=True)` gives an iterator suitable for
SSE. Add a "Runs" page or a "Recent runs" panel on `/pipeline`.

---

### 5. Hub's `routes/stack.py` PROFILES list duplicates `deploy.py`'s

**Why:** the hub doesn't import from `open_pulse` at runtime (so the image
build doesn't have to ship the package as a dep of itself in two places).
The trade-off was a hand-maintained `PROFILES` list. New profiles will
silently fail to appear in the Stack page until someone updates both
files.

**How to apply:** the hub IS now part of the package
(`src/open_pulse/gui/hub/`). Import `_PROFILES` and `_PROFILE_DESCRIPTIONS`
directly from `open_pulse.commands.deploy` and delete the duplicate. Ten
lines of cleanup; no behavioral change.

---

### 6. Theme polish for the rewritten pages

**Why:** during the design pass I rewrote the Stack / Projects / Databases /
Pipeline / Logs templates to use the new `card` / `btn` / `pill` / `data`
primitives. They render and respond to the theme switch (verified via HTTP
200 + grep), but I didn't visually QA each one in light mode. Some inline
styles are still scattered (especially in the Projects builder).

**How to apply:** open each page in light + dark and tighten as needed.
Particular smells: hard-coded `style="font-size: 12px; color: var(--fg-muted);"`
should usually become a class. The Stack page's `style="display: grid;
grid-template-columns: 2fr 1fr;"` could become `.grid-stack` in `app.css`.

---

### 7. Unauthenticated `/static/*` exposure

**Why:** the hub's `app.css` is served via `StaticFiles` mounted at
`/static`, which is open by design (CSS, JS, images need to be reachable
before login or the login page itself looks broken). Confirmed harmless
today, but if anyone adds anything sensitive under `static/` later it'd
leak. Worth a comment near the mount.

**How to apply:** add a one-line comment in `src/open_pulse/gui/hub/main.py`
above `app.mount("/static", …)` explaining the intentional public mount,
plus a CONTRIBUTING-style note under `tools/images/README.md` reminding
people not to put secrets there.

---

## Low / nice-to-have

### 8. Hub favicon + page title

`<title>{% block title %}Open Pulse Hub{% endblock %}</title>` is generic;
no favicon shipped. Add `favicon.ico` to `src/open_pulse/gui/hub/static/`
and link it from `base.html`. The brand-pulse SVG already in the sidebar
is a fine starting point.

### 9. Stack page command output streaming

Currently `POST /api/stack/up` blocks until `docker compose up -d` returns,
then dumps the whole output. For larger profile sets this looks frozen.
Same SSE pattern as #4 fixes both. Lower priority than #4.

### 10. Marquee uses `color-mix(in srgb, …)`

Modern browsers only (Chrome 111+, Firefox 113+, Safari 16.2+). If the hub
ever needs to support older clients (unlikely for a single-tenant control
plane), add a fallback in `static/app.css`.

### 11. `infra/services/tentris-server/`

The `tentris-server/` directory still exists under `infra/services/`,
leftover from the pre-rename era. Currently unused (the SPARQL store role
is filled by Oxigraph behind the sparql-proxy). Decide: archive
(`docs-site/legacy/` link) or delete.

### 12. `src/open_pulse/grimoire/` stub

After the hub move, the only thing left at `src/open_pulse/grimoire/` is a
single-line `__init__.py`. Nothing imports `open_pulse.grimoire`. Delete
the directory; the only effect is one fewer empty namespace.

### 13. CLI completion install

`open-pulse --install-completion` is a Typer freebie. Document in the README
under Quick Start so new users don't miss it.

---

## Out of scope (mentioned in passing, not planned)

- Migrating the hub frontend to React + shadcn/ui. Decided against in the
  design conversation — htmx-class polish was sufficient with Tailwind +
  Alpine and the static-asset story.
- A full RBAC / multi-user auth model for the hub. Single-tenant by design;
  if this changes, swap `auth.py` for an OIDC dependency.
