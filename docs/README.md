# Open Pulse GitHub Pages Landing

> Status: this folder is the legacy static landing page while documentation source of truth is being migrated to `docs-site/` (Docusaurus).
>
> Migration map and branch responsibilities are documented in:
>
> - `docs-site/docs/operations/migration-from-static-docs.md`
> - `docs-site/docs/operations/branch-model.md`

This folder contains the static landing page for Open Pulse, designed for GitHub Pages with a desktop-first layout, an adaptive scroll mode for smaller/short viewports, and a subtle animated background.

## Purpose

The page is a single-entry gateway that presents:

- What Open Pulse is
- Institutional collaboration context
- Core repositories
- News timeline
- Team members
- Collaborations
- Contact and funding acknowledgments

It is intentionally minimalist, technical, and slightly "hacky", with dark mode as default.

## Stack

- Static HTML, CSS, and vanilla JavaScript
- No frontend framework
- No external JS dependencies
- Google Fonts only

## Directory Structure

```text
docs/
  index.html
  README.md
  assets/
    css/
      style.css
    js/
      theme.js
      animation.js
      main.js
  statics/
    Logo_EPFL_2019.svg
    SDSC_Logo_White.svg
    swissuniversities-seeklogo.svg
    eth-board-seeklogo-2.svg
```

## Local Development

From repository root:

```bash
python3 -m http.server 4173 --directory docs
```

Then open:

```text
http://localhost:4173
```

Stop server with `Ctrl+C`.

## GitHub Pages Deployment

In repository settings:

1. Open `Settings` -> `Pages`.
2. Set `Source` to `Deploy from a branch`.
3. Choose your branch (for example `main`).
4. Set folder to `/docs`.
5. Save.

GitHub Pages will serve `docs/index.html` as the landing page.

## Core Files and Responsibilities

- `docs/index.html`
  - Semantic content and layout sections
  - All project links, people, news, repositories, and contact information
  - Canvas layer and DOM hooks:
    - `#theme-toggle`
    - `#constellation-canvas`
    - `#clone-copy-btn`

- `docs/assets/css/style.css`
  - Visual system (colors, gradients, typography)
  - Dark/light theme variable sets
  - Locked desktop no-scroll behavior on large fine-pointer viewports
  - Scroll-safe stacked behavior for narrow, short, or coarse-pointer viewports
  - Marquee touch gesture handling (`touch-action: pan-y`) for immediate vertical scroll
  - Hero sizing and layout balance
  - Floating (unboxed) three-column style

- `docs/assets/js/theme.js`
  - Theme initialization and persistence
  - Theme is dark by default unless user previously selected a theme
  - Uses localStorage key: `open-pulse-theme`
  - Toggle logic for `#theme-toggle`

- `docs/assets/js/animation.js`
  - Background canvas animation:
    - stars
    - graph nodes and edges
    - occasional pulse rays
    - floating ASCII "astronaut" nodes
  - Adaptive scene profile for desktop vs constrained viewports
  - Coarse-pointer frame-rate cap (~30 FPS) and debounced resize handling
  - Reacts to theme changes via `openpulse:themechange`
  - Supports `prefers-reduced-motion`

- `docs/assets/js/main.js`
  - Copy-to-clipboard interaction for clone command
  - Success/failure button feedback

## Content Maintenance Guide

Update content directly in `docs/index.html`.

### Main Content Sections

- Marquee banner (ongoing project message and institution links)
- Hero title and summary
- Clone command and repository link
- Repositories list
- News timeline
- People list (grouped by role/team)
- Collaborations list
- Footer contacts and last update date
- Institution and grant logos

### Current Data Blocks to Keep Updated

- Last update date in footer
- News entries order (newest first)
- Team/role changes
- Repository visibility status (for example "Public soon")
- Contact emails

## Theme Behavior

- Default theme: `dark`
- User-selected theme is saved in localStorage (`open-pulse-theme`)
- Manual toggle in top-right corner
- Light mode uses the same structure with adjusted contrast and logo filtering

If you want to reset saved theme:

```js
localStorage.removeItem("open-pulse-theme")
```

## Layout Rules

- Locked desktop mode (`min-width: 1200px` and `min-height: 761px` and `pointer: fine`)
  - Full viewport layout
  - No page scroll
  - Hero centered around middle/upper-mid region
  - Lists shown in three columns

- Scroll-safe responsive mode (`max-width: 1199px`, or `max-height: 760px`, or `pointer: coarse`)
  - Vertical stacking
  - Page scroll enabled
  - Explicit reset of fixed desktop viewport constraints (`height: auto`, `overflow: visible`)
  - Footer becomes multi-line for readability
  - Marquee allows immediate vertical gesture start (`touch-action: pan-y`)

## Animation Tuning Notes

Key parameters in `docs/assets/js/animation.js`:

- Scene density profile:
  - Desktop and constrained-view profiles selected via `MOBILE_QUERY`
  - Density controls: `starMin/starMax/starDivisor`, `nodeMin/nodeMax/nodeDivisor`, `astronautMin/astronautMax/astronautDivisor`
- Connection visibility:
  - `threshold`
  - connection alpha and line width
- Pulse activity:
  - interval (`pulseIntervalMs`)
  - probability (`pulseChance`)
  - pulse speed range (`pulseSpeedMin` / `pulseSpeedMax`)
- Motion/performance controls:
  - coarse-pointer frame cap (`MOBILE_MIN_FRAME_DELTA`)
  - debounced resize (`RESIZE_DEBOUNCE_MS`)
  - smart resize re-init thresholds (`MOBILE_SMALL_HEIGHT_DELTA`, `MOBILE_AREA_REINIT_RATIO`)
- Layer mood:
  - `drawNebula()` gradients
  - CSS canvas opacity and blend mode in `style.css`

## Accessibility and Motion

- `prefers-reduced-motion: reduce` is respected in CSS and JS
- External links use visible hover/focus treatment
- Theme toggle includes ARIA attributes
- Canvas is decorative (`aria-hidden="true"`)

## Troubleshooting

- Background animation not visible:
  - Confirm `animation.js` is loading
  - Check browser console errors
  - Ensure canvas is not hidden by custom CSS changes

- Theme not changing:
  - Confirm `#theme-toggle` exists in `index.html`
  - Check localStorage and clear `open-pulse-theme` if needed

- Old styles still shown:
  - Hard refresh browser cache
  - Verify GitHub Pages has rebuilt after push

- Scroll feels like it needs two gestures on reduced windows:
  - Verify viewport crosses into scroll-safe mode (`max-width: 1199px` or `max-height: 760px`)
  - Check computed styles for `.viewport`: `height: auto` and `overflow: visible`
  - Confirm desktop locked mode applies only on large fine-pointer viewports

## Change Checklist

Before committing landing-page edits:

- Verify links and emails
- Verify desktop no-scroll behavior
- Verify mobile stacking and readability
- Verify reduced-size desktop windows scroll on first wheel/trackpad gesture
- Verify dark and light theme contrast
- Verify marquee animation and clone copy button
- Verify footer logos are visible in both themes
