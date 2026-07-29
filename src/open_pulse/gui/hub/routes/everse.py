"""EVERSE research-software-quality indicators — OpenSSF Scorecard-backed slice.

Maps OpenSSF Scorecard checks (https://securityscorecards.dev) onto the EVERSE
RSQI indicators for the security / static-analysis / practice families — the
ones OpenPulse cannot derive from its own metadata graph. Scorecard runs over
the GitHub API (no source checkout / build), so this reaches indicators like
"does the project use SAST / fuzzing / dependency updates / code review"
*without* standing up a CodeQL pipeline.

Coverage: the public cached API (``api.securityscorecards.dev``) has results for
repositories in the OpenSSF weekly run; anything else returns ``covered: false``.
Full coverage of arbitrary repos is a follow-up (run the ``scorecard`` CLI
on-demand with the hub's GitHub token — still no source build).
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends

from ..auth import maybe_require_auth

router = APIRouter(prefix="/api/v1/indicators", tags=["indicators"])

_SCORECARD_API = "https://api.securityscorecards.dev/projects/github.com"

# Scorecard check name -> (EVERSE abbreviation, EVERSE name, quality dimension).
# Only checks with a 1:1 EVERSE RSQI indicator are mapped; the rest ride along
# as ``extra_checks`` (security hygiene with no direct EVERSE indicator).
_SCORECARD_MAP: dict[str, tuple[str, str, str]] = {
    "SAST": (
        "static_analysis_common_vulnerabilities",
        "Has static analysis for common vulnerabilities",
        "security",
    ),
    "Vulnerabilities": (
        "no_critical_vulnerability",
        "No critical vulnerabilities",
        "security",
    ),
    "Binary-Artifacts": (
        "has_no_binary_artifacts",
        "Project repository has no binary artifacts",
        "security",
    ),
    "Fuzzing": ("uses_fuzzing", "Software uses fuzzing", "safety"),
    "Code-Review": (
        "human_code_review_requirement",
        "Software requires human code review",
        "functional_suitability",
    ),
    "CI-Tests": (
        "has_ci-tests",
        "Software has continuous integration tests",
        "maintainability",
    ),
    "Dependency-Update-Tool": (
        "dependency_management",
        "Software has dependency management solution",
        "sustainability",
    ),
    "License": ("software_has_license", "Software has license", "fairness"),
    "Maintained": (
        "project_is_active",
        "Project repository is active",
        "maintainability",
    ),
    "Packaging": (
        "has_published_package",
        "Software is published as a downloadable package",
        "flexibility",
    ),
    "Contributors": (
        "has_active_contributors",
        "Project has active contributors beyond core maintainers",
        "community",
    ),
}

# Scorecard scores run 0-10 (-1 = inconclusive / no access). At/above this a
# check counts as a passing EVERSE indicator.
_PASS_THRESHOLD = 7


def _fetch_scorecard(owner: str, repo: str) -> dict[str, Any] | None:
    """The public cached Scorecard result, or ``None`` if uncached/unreachable."""
    try:
        resp = httpx.get(
            f"{_SCORECARD_API}/{owner}/{repo}",
            timeout=15.0,
            headers={"Accept": "application/json"},
        )
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


@router.get(
    "/everse/github.com/{owner}/{repo}",
    dependencies=[Depends(maybe_require_auth)],
)
def everse_indicators(owner: str, repo: str) -> dict[str, Any]:
    """EVERSE RSQI indicators for a repository, derived from OpenSSF Scorecard.

    Returns the security / static-analysis / practice indicators Scorecard can
    determine over the GitHub API. ``covered=false`` when Scorecard has no
    cached result for the repo."""
    repo_url = f"github.com/{owner}/{repo}"
    data = _fetch_scorecard(owner, repo)
    if data is None:
        return {
            "repo": repo_url,
            "covered": False,
            "source": "OpenSSF Scorecard",
            "note": "No cached Scorecard result for this repository.",
            "indicators": [],
        }

    indicators: list[dict[str, Any]] = []
    extra: list[dict[str, Any]] = []
    for check in data.get("checks", []):
        name = check.get("name")
        score = check.get("score")
        mapped = _SCORECARD_MAP.get(name)
        if mapped is None:
            extra.append(
                {"check": name, "score": score, "reason": check.get("reason")}
            )
            continue
        abbr, ename, dim = mapped
        passed = None if score is None or score < 0 else score >= _PASS_THRESHOLD
        indicators.append(
            {
                "@type": "SoftwareQualityIndicator",
                "abbreviation": abbr,
                "name": ename,
                "qualityDimension": dim,
                "value": score,  # 0-10, or -1 when inconclusive
                "passed": passed,  # bool, or null when inconclusive
                "source": "OpenSSF Scorecard",
                "check": name,
            }
        )

    return {
        "repo": repo_url,
        "covered": True,
        "source": "OpenSSF Scorecard",
        "scorecard_score": data.get("score"),
        "scorecard_date": data.get("date"),
        "indicators": indicators,
        "extra_checks": extra,
    }
