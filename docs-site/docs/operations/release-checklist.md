---
title: Release Checklist
slug: /operations/release-checklist
---

# Release Checklist

Use this checklist for stable releases created from semver tags (`vX.Y.Z`).

## Branch protection baseline (`main`)

- Block direct pushes for non-admin users.
- Require pull request approval before merge.
- Require branch to be up to date before merge.
- Require these status checks:
  - `ci`
  - `docker-validate`
  - `docs-build`

## Release preparation

- Confirm all required checks are green on the merge commit.
- Confirm `CHANGELOG.md` has an `[Unreleased]` update ready to cut into a version.
- Verify release tag target commit is on `main`.

## Release execution

- Create and push a stable semver tag:
  - `git tag vX.Y.Z`
  - `git push origin vX.Y.Z`
- Verify the `Release` workflow starts and passes.
- Confirm expected artifacts are attached to the draft release:
  - `analysis-vX.Y.Z.tar`
  - `devcontainer-vX.Y.Z.tar`
  - `SHA256SUMS.txt`
  - `openpulse_analysis-*.whl`

## Release finalization

- Review auto-generated release notes and adjust as needed.
- Publish the draft release.
- Move `CHANGELOG.md` entries from `[Unreleased]` to the new version section.
