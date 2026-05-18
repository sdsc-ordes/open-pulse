"""Runtime configuration for the hub service.

All settings come from environment variables so the container can be
configured purely via compose. The hub deliberately avoids reading the
project's `.env` directly — the compose layer already loads it.

The OpenSearch credentials here are *server-side fallbacks* — the
Settings panel in the browser also stores per-user keys in
``localStorage`` and the Databases console will prefer those when the
user has set them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    auth_token: str
    """Single shared password. Set via HUB_AUTH; required."""

    data_dir: Path
    """Where the hub keeps its SQLite app DB and DuckDB scratch files."""

    applier_url: str
    """Base URL of the projects-applier sidecar (in-network)."""

    sparql_url: str
    """Base URL of the SPARQL store (sparql-proxy in-network)."""

    neo4j_url: str
    """Bolt URL of the Neo4j instance (in-network)."""

    grimoire_kibiter_url: str
    """External URL of the GrimoireLab Kibiter / OpenSearch Dashboards."""

    neo4j_browser_url: str
    """User-facing URL of the Neo4j Browser (host-published port)."""

    sparql_browser_url: str
    """User-facing URL of the SPARQL store (Oxigraph behind sparql-proxy)."""

    opensearch_dashboards_url: str
    """User-facing URL of OpenSearch Dashboards."""

    ontology_url: str
    """External URL of the open-pulse ontology documentation."""

    crawler_docs_url: str
    """User-facing URL of the crawler's OpenAPI / Swagger docs (host-published)."""

    extractor_docs_url: str
    """User-facing URL of the git-metadata-extractor's Swagger docs
    (host-published)."""

    opensearch_url: str
    """Base URL of OpenSearch (in-network)."""

    opensearch_username: str
    """OpenSearch user; default 'admin'. Per-request creds from the Settings
    page override this."""

    opensearch_password: str
    """OpenSearch password; per-request creds from the Settings page
    override this."""

    opensearch_verify_tls: bool
    """Verify the TLS cert when connecting to OpenSearch. Defaults to false
    because the GrimoireLab compose ships a self-signed cert."""

    applier_auth: str
    """Default bearer for the projects-applier sidecar. Read from APPLIER_AUTH
    in the hub container's env (sourced from the project's .env). The
    Settings page can override this per-browser for users targeting a
    *remote* GrimoireLab; for the local deployment we just pick it up
    automatically and the user never has to type it."""

    sparql_user: str
    """SPARQL Basic-auth user, parsed from SPARQL_AUTH (`user/password`).
    Settings page override wins."""

    sparql_password: str
    """SPARQL Basic-auth password, parsed from SPARQL_AUTH."""

    neo4j_user: str
    """Neo4j user, parsed from NEO4J_AUTH (`user/password`)."""

    neo4j_password: str
    """Neo4j password, parsed from NEO4J_AUTH."""


def _parse_user_pass(raw: str) -> tuple[str, str]:
    """Split a `user/password` env value (used by SPARQL_AUTH).

    Returns ``("", "")`` if unset or malformed; ``("", raw)`` if there's
    no slash (treats the whole thing as a password — matches the
    runner's loose handling).
    """
    if not raw:
        return "", ""
    if "/" in raw:
        u, p = raw.split("/", 1)
        return u.strip(), p.strip()
    return "", raw.strip()


def load_settings() -> Settings:
    auth = os.environ.get("HUB_AUTH", "").strip()
    if not auth:
        raise RuntimeError(
            "HUB_AUTH is required. Set a strong password in your .env "
            "(e.g. python -c 'import secrets; print(secrets.token_urlsafe(32))')."
        )

    # User-facing hostname for the host-published service ports. The
    # individual HUB_*_BROWSER_URL / HUB_*_DOCS_URL overrides still win;
    # this is just the default so an operator only has to set one var.
    public_host = os.environ.get("HUB_PUBLIC_HOST", "localhost").strip() or "localhost"

    return Settings(
        auth_token=auth,
        data_dir=Path(os.environ.get("HUB_DATA_DIR", "/data/hub")),
        applier_url=os.environ.get("HUB_APPLIER_URL", "http://projects-applier:8000"),
        sparql_url=os.environ.get("HUB_SPARQL_URL", "http://sparql-proxy:7878"),
        neo4j_url=os.environ.get("HUB_NEO4J_URL", "bolt://neo4j:7687"),
        grimoire_kibiter_url=os.environ.get(
            "HUB_KIBITER_URL", f"http://{public_host}:7508"
        ),
        opensearch_url=os.environ.get(
            "HUB_OPENSEARCH_URL", "https://opensearch-node1:9200"
        ),
        opensearch_username=os.environ.get(
            "HUB_OPENSEARCH_USERNAME", os.environ.get("OPENSEARCH_USERNAME", "admin")
        ),
        opensearch_password=os.environ.get(
            "HUB_OPENSEARCH_PASSWORD", os.environ.get("OPENSEARCH_PASSWORD", "")
        ),
        opensearch_verify_tls=_env_bool("HUB_OPENSEARCH_VERIFY_TLS", default=False),
        neo4j_browser_url=os.environ.get(
            "HUB_NEO4J_BROWSER_URL", f"http://{public_host}:7503"
        ),
        sparql_browser_url=os.environ.get(
            "HUB_SPARQL_BROWSER_URL", f"http://{public_host}:7502"
        ),
        opensearch_dashboards_url=os.environ.get(
            "HUB_OPENSEARCH_DASHBOARDS_URL", f"http://{public_host}:7508"
        ),
        ontology_url=os.environ.get(
            "HUB_ONTOLOGY_URL", "https://github.com/sdsc-ordes/open-pulse-ontology"
        ),
        # Default to the hub-proxied Swagger surfaces (routes/crawler.py +
        # routes/extractor.py) — hub auth gates discovery, so we don't have
        # to expose the upstream Swagger pages anonymously on their public
        # ports. Override with a full URL to bypass the proxy.
        crawler_docs_url=os.environ.get("HUB_CRAWLER_DOCS_URL", "/api/crawler/docs"),
        extractor_docs_url=os.environ.get(
            "HUB_EXTRACTOR_DOCS_URL", "/api/extractor/docs"
        ),
        applier_auth=os.environ.get("APPLIER_AUTH", "").strip(),
        sparql_user=(_parse_user_pass(os.environ.get("SPARQL_AUTH", ""))[0]),
        sparql_password=(_parse_user_pass(os.environ.get("SPARQL_AUTH", ""))[1]),
        neo4j_user=(_parse_user_pass(os.environ.get("NEO4J_AUTH", ""))[0]),
        neo4j_password=(_parse_user_pass(os.environ.get("NEO4J_AUTH", ""))[1]),
    )
