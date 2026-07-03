"""Activity command group — poll GitHub repository activity and append a
weekly JSONL change log.

The cheap design (see dev notes): we never GET one request per repo. For each
owner we list their repos sorted by ``pushed`` descending and early-stop once a
repo's ``pushed_at`` falls at/under our watermark — so a list call costs
O(movers), not O(repos). Conditional requests (``If-None-Match``) make
unchanged owners return ``304`` which does NOT count against the rate limit.
Only the movers get a ``/activity`` fetch, whose push/force-push events are
appended to ``activity-<ISO-year>-W<week>.jsonl``.

Start scope: pushes only (``push`` + ``force_push``). Branch lifecycle and
merge events can be enabled later via ``--activity-types``.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
import httpx
import typer
from rich.console import Console

console = Console(stderr=True)
app = typer.Typer(help="Poll GitHub repository activity (pushes) → weekly JSONL.")

# Token is resolved from the first env var that is set; a general API token is
# preferred, falling back to the crawler's token so the CLI can reuse it.
_TOKEN_ENV_ORDER = ("GITHUB_API_TOKEN", "CRAWLER_GITHUB_TOKEN", "GITHUB_TOKEN")
_DEFAULT_INDEX = "data/index/github_repos/duckdb/github_repos.ro.duckdb"
_DEFAULT_OUT = "data/activity"
_PUSH_TYPES = {"push", "force_push"}
_API = "https://api.github.com"
_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def _resolve_token(explicit: str | None) -> str:
    if explicit:
        return explicit
    for var in _TOKEN_ENV_ORDER:
        val = os.environ.get(var, "").strip()
        if val:
            return val
    raise typer.BadParameter(
        f"No GitHub token. Pass --token or set one of {', '.join(_TOKEN_ENV_ORDER)}."
    )


def _iso_week_file(out: Path, now: datetime) -> Path:
    year, week, _ = now.isocalendar()
    return out / f"activity-{year}-W{week:02d}.jsonl"


def _load_state(out: Path) -> dict[str, Any]:
    f = out / "state.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except (ValueError, OSError):
            console.print("[yellow]state.json unreadable — starting fresh[/]")
    return {"last_run": None, "owners": {}}


def _save_state(out: Path, state: dict[str, Any]) -> None:
    (out / "state.json").write_text(json.dumps(state, indent=2, sort_keys=True))


def _index_repos(index: Path) -> dict[str, set[str]]:
    """``{owner: {name, ...}}`` for every repo we track, from the index."""
    con = duckdb.connect(str(index), read_only=True)
    try:
        rows = con.execute(
            "SELECT owner, name FROM repos WHERE owner IS NOT NULL AND name IS NOT NULL"
        ).fetchall()
    finally:
        con.close()
    owners: dict[str, set[str]] = {}
    for owner, name in rows:
        owners.setdefault(owner, set()).add(name)
    return owners


def _respect_rate_limit(resp: httpx.Response) -> None:
    """Sleep when the primary budget is exhausted or a secondary limit hits."""
    if resp.status_code == 403 and "Retry-After" in resp.headers:
        time.sleep(int(resp.headers["Retry-After"]) + 1)
        return
    if resp.headers.get("X-RateLimit-Remaining") == "0":
        reset = int(resp.headers.get("X-RateLimit-Reset", "0"))
        wait = max(0, reset - int(time.time())) + 1
        console.print(f"[yellow]rate limit hit — sleeping {wait}s[/]")
        time.sleep(wait)


def _list_owner_movers(
    client: httpx.Client,
    owner: str,
    owner_state: dict[str, Any],
    watermark: datetime,
    tracked: set[str],
) -> tuple[list[dict[str, Any]], bool]:
    """Repos for ``owner`` pushed after ``watermark``, via list-by-pushed +
    early-stop. Returns (movers, owner_unchanged). Updates ``owner_state`` etag.

    ``owner_state`` carries ``{kind: 'orgs'|'users', etag}`` — kind is cached so
    we don't re-probe org-vs-user every run.
    """
    kind = owner_state.get("kind")
    kinds = [kind] if kind else ["orgs", "users"]
    movers: list[dict[str, Any]] = []
    for k in kinds:
        url = f"{_API}/{k}/{owner}/repos"
        params = {"sort": "pushed", "direction": "desc", "per_page": "100"}
        headers = dict(_HEADERS)
        # Conditional only on the first page of the cached kind — a 304 there
        # means nothing in this owner moved.
        if owner_state.get("etag") and k == kind:
            headers["If-None-Match"] = owner_state["etag"]
        resp = client.get(url, params=params, headers=headers)
        if resp.status_code == 304:
            return [], True
        if resp.status_code == 404:
            continue  # not this kind (org vs user) — try the next
        if resp.status_code != 200:
            _respect_rate_limit(resp)
            return [], False
        owner_state["kind"] = k
        owner_state["etag"] = resp.headers.get("ETag", owner_state.get("etag"))
        stop = False
        while True:
            for repo in resp.json():
                pushed = repo.get("pushed_at")
                if not pushed:
                    continue
                if datetime.fromisoformat(pushed.replace("Z", "+00:00")) <= watermark:
                    stop = True
                    break
                if repo["name"] in tracked:
                    movers.append(
                        {"name": repo["name"], "pushed_at": pushed,
                         "default_branch": repo.get("default_branch")}
                    )
            nxt = resp.links.get("next", {}).get("url")
            if stop or not nxt:
                break
            resp = client.get(nxt, headers=_HEADERS)
            if resp.status_code != 200:
                _respect_rate_limit(resp)
                break
        return movers, False
    return movers, False


def _fetch_pushes(
    client: httpx.Client, owner: str, name: str, types: set[str]
) -> list[dict[str, Any]]:
    """The repo's activity events of interest for the last week."""
    url = f"{_API}/repos/{owner}/{name}/activity"
    resp = client.get(
        url, params={"time_period": "week", "per_page": "100"}, headers=_HEADERS
    )
    if resp.status_code != 200:
        _respect_rate_limit(resp)
        return []
    out = []
    for ev in resp.json():
        if ev.get("activity_type") not in types:
            continue
        out.append(
            {
                "repo": f"{owner}/{name}",
                "activity_type": ev.get("activity_type"),
                "ref": ev.get("ref"),
                "before": ev.get("before"),
                "after": ev.get("after"),
                "timestamp": ev.get("timestamp"),
                "actor": (ev.get("actor") or {}).get("login"),
            }
        )
    return out


@app.command()
def sync(
    token: str = typer.Option(None, help="GitHub token (else env GITHUB_API_TOKEN/…)."),
    index: Path = typer.Option(Path(_DEFAULT_INDEX), help="github_repos DuckDB index."),
    out: Path = typer.Option(Path(_DEFAULT_OUT), help="Output dir (JSONL + state)."),
    window_days: int = typer.Option(7, help="First-run lookback when no state."),
    owner: str = typer.Option(None, help="Limit to a single owner (debug)."),
    limit_owners: int = typer.Option(0, help="Cap owners scanned (0 = all; debug)."),
    dry_run: bool = typer.Option(False, help="Detect movers; don't fetch/write."),
) -> None:
    """Detect pushed repos since the last run and append their push activity."""
    tok = _resolve_token(token)
    if not index.exists():
        raise typer.BadParameter(f"index not found: {index}")
    if not dry_run:
        out.mkdir(parents=True, exist_ok=True)

    state = _load_state(out)
    now = datetime.now(timezone.utc)
    watermark = (
        datetime.fromisoformat(state["last_run"])
        if state.get("last_run")
        else now - timedelta(days=window_days)
    )

    owners = _index_repos(index)
    names = sorted(owners) if not owner else [owner] if owner in owners else []
    if limit_owners:
        names = names[:limit_owners]
    console.print(
        f"Scanning {len(names)} owners (of {len(owners)}); "
        f"watermark={watermark.isoformat()}"
    )

    movers_total = events_total = unchanged = 0
    state.setdefault("owners", {})
    auth = {"Authorization": f"Bearer {tok}"}
    with httpx.Client(timeout=30.0, headers=auth, follow_redirects=True) as client:
        jsonl = _iso_week_file(out, now)
        sink = None if dry_run else jsonl.open("a")
        try:
            for i, own in enumerate(names, 1):
                os_state = state["owners"].setdefault(own, {})
                movers, same = _list_owner_movers(
                    client, own, os_state, watermark, owners[own]
                )
                if same:
                    unchanged += 1
                    continue
                for m in movers:
                    movers_total += 1
                    if dry_run:
                        console.print(f"  [cyan]mover[/] {own}/{m['name']} "
                                      f"pushed_at={m['pushed_at']}")
                        continue
                    for ev in _fetch_pushes(client, own, m["name"], _PUSH_TYPES):
                        sink.write(json.dumps(ev) + "\n")
                        events_total += 1
                if i % 500 == 0:
                    console.print(f"  …{i}/{len(names)} owners")
        finally:
            if sink:
                sink.close()

    if not dry_run:
        state["last_run"] = now.isoformat()
        _save_state(out, state)

    console.print(
        f"[green]done[/] owners_unchanged={unchanged} movers={movers_total} "
        f"events={events_total}"
        + (f" → {jsonl}" if not dry_run else " (dry-run)")
    )
