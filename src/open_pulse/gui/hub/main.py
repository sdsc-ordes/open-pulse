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

from .auth import _COOKIE_NAME, clear_session, get_settings, require_auth
from .chaoss import routes as chaoss_routes
from .routes import (
    admin,
    crawler,
    databases,
    hub,
    pipeline,
    projects,
    services,
    stack,
    stats,
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


app = FastAPI(title="open-pulse-hub", docs_url=None, redoc_url=None, lifespan=_lifespan)

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

app.include_router(services.router)
app.include_router(projects.router)
app.include_router(databases.router)
app.include_router(pipeline.router)
app.include_router(stack.router)
app.include_router(stats.router)
app.include_router(crawler.router)
app.include_router(admin.router)
app.include_router(hub.router)
app.include_router(hub.api)
app.include_router(chaoss_routes.router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Public liveness — used by the compose healthcheck."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def overview_page(request: Request, _: None = Depends(require_auth)) -> HTMLResponse:
    """New Overview: time-series charts of the metrics the marquee tracks."""
    return templates.TemplateResponse(request, "overview.html", {"page": "overview"})


@app.get("/status", response_class=HTMLResponse)
def status_page(request: Request, _: None = Depends(require_auth)) -> HTMLResponse:
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
def services_page(request: Request, _: None = Depends(require_auth)) -> HTMLResponse:
    return templates.TemplateResponse(request, "services.html", {"page": "services"})


@app.get("/projects", response_class=HTMLResponse)
def projects_page(request: Request, _: None = Depends(require_auth)) -> HTMLResponse:
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


@app.get("/pipeline", response_class=HTMLResponse)
def pipeline_page(request: Request, _: None = Depends(require_auth)) -> HTMLResponse:
    return templates.TemplateResponse(request, "pipeline.html", {"page": "pipeline"})


@app.get("/stack", response_class=HTMLResponse)
def stack_page(request: Request, _: None = Depends(require_auth)) -> HTMLResponse:
    return templates.TemplateResponse(request, "stack.html", {"page": "stack"})


@app.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request, _: None = Depends(require_auth)) -> HTMLResponse:
    return templates.TemplateResponse(request, "logs.html", {"page": "logs"})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, _: None = Depends(require_auth)) -> HTMLResponse:
    return templates.TemplateResponse(request, "settings.html", {"page": "settings"})


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, _: None = Depends(require_auth)) -> HTMLResponse:
    """Resources dashboard — disk / RAM / CPU / docker. Polls every 15 min."""
    return templates.TemplateResponse(request, "admin.html", {"page": "admin"})


@app.post("/logout")
def logout(response: Response, request: Request) -> RedirectResponse:
    clear_session(response, request.cookies.get(_COOKIE_NAME))
    redirect = RedirectResponse(url="/", status_code=303)
    redirect.delete_cookie(_COOKIE_NAME)
    return redirect
