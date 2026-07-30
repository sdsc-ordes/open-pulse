"""FastAPI application entry point for the open-pulse hub."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .auth import (
    _COOKIE_NAME,
    clear_session,
    get_settings,
    require_admin,
    require_auth,
)
from .chaoss import routes as chaoss_routes
from .routes import (
    admin,
    ai,
    canvas,
    crawler,
    databases,
    everse,
    extractor,
    hub,
    login,
    pipeline,
    projects,
    services,
    stack,
    stats,
    users,
)

_HERE = Path(__file__).parent
log = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Spawn the metrics sampler on startup, cancel on shutdown."""
    task = asyncio.create_task(stats.metrics_history_loop(), name="metrics-history")
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            log.exception("metrics history loop terminated abnormally")


# Swagger / OpenAPI for the hub's own API (metrics, catalog, knowledge
# graph, …) served under /api so it sits alongside the other service
# docs in the sidebar's "API docs" group.
app = FastAPI(
    title="OpenPulse API",
    description=(
        "The OpenPulse hub API — CHAOSS metrics, the entity catalog, "
        "knowledge-graph resolution and the data-plane proxies."
    ),
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=_lifespan,
)

app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
templates = Jinja2Templates(directory=str(_HERE / "templates"))

# Globals every template can read directly — keeps individual page handlers
# from each having to thread these through their context dicts.
_settings = get_settings()
templates.env.globals["dashboards"] = [
    {
        "name": "Neo4j",
        "tech": "Graph",
        "url": _settings.neo4j_browser_url,
        "summary": "Community network — graph browser for the crawled repos.",
    },
    {
        "name": "Oxigraph",
        "tech": "RDF / SPARQL",
        "url": _settings.sparql_browser_url,
        "summary": "Repository properties (licenses, languages, topics, …).",
    },
    {
        "name": "OpenSearch",
        "tech": "GrimoireLab",
        "url": _settings.opensearch_dashboards_url,
        "summary": "Community monitoring + GrimoireLab dashboards.",
    },
]
templates.env.globals["ontology_url"] = _settings.ontology_url
# Swagger UIs for the two pipeline services that ship an HTTP API. Rendered
# as their own compact sidebar group so users can poke the endpoints
# directly without digging through Portainer.
templates.env.globals["api_docs"] = [
    {
        "name": "OpenPulse API",
        "tech": "Metrics + hub",
        "url": "/api/docs",
    },
    {
        "name": "Crawler",
        "tech": "FastAPI",
        "url": _settings.crawler_docs_url,
    },
    {
        "name": "Metadata extractor",
        "tech": "FastAPI",
        "url": _settings.extractor_docs_url,
    },
]
# Per-service URLs exposed as flat template globals so any page (in
# particular ``status.html`` Quick-links) can link out to a real
# host:port without hardcoding ``localhost``. Each value already
# accounts for ``HUB_PUBLIC_HOST`` via the compose interpolation, so a
# future hostname change only needs the env var, never a template edit.
templates.env.globals["neo4j_browser_url"] = _settings.neo4j_browser_url
templates.env.globals["sparql_browser_url"] = _settings.sparql_browser_url
templates.env.globals["opensearch_dashboards_url"] = _settings.opensearch_dashboards_url
templates.env.globals["crawler_docs_url"] = _settings.crawler_docs_url
templates.env.globals["extractor_docs_url"] = _settings.extractor_docs_url
# Surface HUB_READONLY to every template so the sidebar / action buttons
# can hide themselves when the deploy is locked down. The matching
# auth.require_writable dependency enforces the same gate on every
# mutating endpoint, so the UI hint and the server contract can't drift.
templates.env.globals["hub_read_only"] = _settings.read_only


# Several route modules ship their own Jinja2Templates instance
# because they need module-local filters (the CHAOSS surface adds an
# inline-markdown ``md`` filter; routes/hub.py registered its own
# directory historically). Without propagating the shared globals,
# ``base.html``'s sidebar renders blank Dashboards + an empty
# ontology link on those pages.
#
# Pushing globals via setdefault means each instance keeps its own
# overrides; if a route registered a same-named global before this
# line ran, it wins.
def _propagate_globals(target: Jinja2Templates) -> None:
    """Mirror every key on the shared template env onto another
    Jinja2Templates instance, without clobbering existing keys."""
    for _k, _v in templates.env.globals.items():
        target.env.globals.setdefault(_k, _v)


_propagate_globals(chaoss_routes.templates)
_propagate_globals(hub.templates)
_propagate_globals(canvas.templates)

app.include_router(services.router)
app.include_router(projects.router)
app.include_router(databases.router)
app.include_router(canvas.router)
app.include_router(pipeline.router)
app.include_router(stack.router)
app.include_router(stats.router)
app.include_router(crawler.router)
app.include_router(extractor.router)
app.include_router(users.router)
app.include_router(ai.router)
app.include_router(admin.router)
app.include_router(everse.router)
app.include_router(hub.router)
app.include_router(hub.api)
app.include_router(chaoss_routes.router)
# Public auth surface: /login (GET + POST) and /logout. These are
# intentionally outside the require_auth dependency tree — anyone can
# reach the form, the POST validates against HUB_AUTH itself.
app.include_router(login.router)


# Browser-friendly 401 handling. The hub used to surface
# `WWW-Authenticate: Basic realm="open-pulse-hub"` and the browser
# popped up its native (ugly) credential dialog. Now: when an HTML
# client hits a 401, redirect to /login with `?next=` set so the user
# lands back where they were trying to go after sign-in. API clients
# (curl, Postman, the v2 SDK) keep getting the bare 401 — they're
# identified by their Accept header not asking for HTML.
@app.exception_handler(401)
async def _login_redirect_for_browsers(request: Request, exc):
    accept = (request.headers.get("accept") or "").lower()
    looks_like_browser = (
        "text/html" in accept
        # Be conservative: an Ajax/fetch caller that signals JSON
        # explicitly stays in the 401 lane even if its Accept fall-back
        # is `*/*` (which would technically include text/html).
        and "application/json" not in accept
        # And browsers always send a Sec-Fetch-Mode of navigate / iframe
        # on top-level navigations; tools like curl don't set it at all.
        # Treat its absence as "ambiguous → fall through to JSON 401".
        and request.headers.get("sec-fetch-mode") is not None
    )
    if looks_like_browser:
        next_url = request.url.path
        if request.url.query:
            next_url += f"?{request.url.query}"
        from urllib.parse import quote
        return RedirectResponse(
            f"/login?next={quote(next_url, safe='/?=&')}",
            status_code=303,
        )
    # Default JSON 401 (re-raises through FastAPI's default handler).
    from fastapi.exception_handlers import http_exception_handler
    from fastapi import HTTPException
    if isinstance(exc, HTTPException):
        return await http_exception_handler(request, exc)
    return Response(
        content='{"detail":"Authentication required"}',
        status_code=401,
        media_type="application/json",
        headers={"WWW-Authenticate": 'Basic realm="open-pulse-hub"'},
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Public liveness — used by the compose healthcheck."""
    return {"status": "ok"}


@app.get("/", response_model=None)
def overview_page(
    request: Request, _: None = Depends(require_auth)
) -> HTMLResponse | RedirectResponse:
    """Landing page. Admins get the Overview (metrics time-series); readers
    are sent straight to the Hub — their home is the knowledge catalog, not
    the operations dashboard."""
    if getattr(request.state, "user_role", "admin") != "admin":
        return RedirectResponse(url="/hub", status_code=307)
    return templates.TemplateResponse(request, "overview.html", {"page": "overview"})


@app.get("/status", response_class=HTMLResponse)
def status_page(
    request: Request,
    _: None = Depends(require_auth),
    __: None = Depends(require_admin),
) -> HTMLResponse:
    """Status: current snapshot of services + quick links (the old Overview)."""
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "status.html",
        {
            "page": "status",
            "kibiter_url": settings.grimoire_kibiter_url,
        },
    )


@app.get("/services", response_class=HTMLResponse)
def services_page(
    request: Request,
    _: None = Depends(require_auth),
    __: None = Depends(require_admin),
) -> HTMLResponse:
    return templates.TemplateResponse(request, "services.html", {"page": "services"})


@app.get("/projects", response_class=HTMLResponse)
def projects_page(
    request: Request,
    _: None = Depends(require_auth),
    __: None = Depends(require_admin),
) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "projects.html",
        {
            "page": "projects",
            "default_sparql": settings.sparql_url,
        },
    )


@app.get("/databases", response_class=HTMLResponse)
def databases_page(request: Request, _: None = Depends(require_auth)) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "databases.html",
        {
            "page": "databases",
            "default_sparql_user": settings.sparql_user,
            "default_sparql_password": settings.sparql_password,
            "default_opensearch_user": settings.opensearch_username,
            "default_opensearch_password": settings.opensearch_password,
            "default_neo4j_user": settings.neo4j_user or "neo4j",
            "default_neo4j_password": settings.neo4j_password,
        },
    )


@app.get("/agent", response_class=HTMLResponse)
def agent_page(request: Request, _: None = Depends(require_auth)) -> HTMLResponse:
    """Agent chat — a full-page assistant with the read-only tool belt
    (SPARQL / Cypher / OpenSearch / DuckDB), rendering markdown, code,
    images, Vega-Lite plots, and sandboxed HTML."""
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "agent.html",
        {
            "page": "agent",
            "default_model": settings.llm_model or "",
        },
    )


@app.get("/pipeline", response_class=HTMLResponse)
def pipeline_page(
    request: Request,
    _: None = Depends(require_auth),
    __: None = Depends(require_admin),
) -> HTMLResponse:
    return templates.TemplateResponse(request, "pipeline.html", {"page": "pipeline"})


@app.get("/stack", response_class=HTMLResponse)
def stack_page(
    request: Request,
    _: None = Depends(require_auth),
    __: None = Depends(require_admin),
) -> HTMLResponse:
    return templates.TemplateResponse(request, "stack.html", {"page": "stack"})


@app.get("/logs", response_class=HTMLResponse)
def logs_page(
    request: Request,
    _: None = Depends(require_auth),
    __: None = Depends(require_admin),
) -> HTMLResponse:
    return templates.TemplateResponse(request, "logs.html", {"page": "logs"})


@app.get("/users", response_class=HTMLResponse)
def users_page(
    request: Request,
    _: None = Depends(require_auth),
    __: None = Depends(require_admin),
) -> HTMLResponse:
    """Admin Users — reader token management + per-token activity."""
    return templates.TemplateResponse(request, "users.html", {"page": "users"})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, _: None = Depends(require_auth)) -> HTMLResponse:
    return templates.TemplateResponse(request, "settings.html", {"page": "settings"})


@app.get("/admin", response_class=HTMLResponse)
def admin_page(
    request: Request,
    _: None = Depends(require_auth),
    __: None = Depends(require_admin),
) -> HTMLResponse:
    """Resources dashboard — disk / RAM / CPU / docker. Polls every 15 min."""
    return templates.TemplateResponse(request, "admin.html", {"page": "admin"})


@app.post("/logout")
def logout(response: Response, request: Request) -> RedirectResponse:
    clear_session(response, request.cookies.get(_COOKIE_NAME))
    redirect = RedirectResponse(url="/", status_code=303)
    redirect.delete_cookie(_COOKIE_NAME)
    return redirect
