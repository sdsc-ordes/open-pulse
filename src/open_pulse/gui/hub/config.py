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
    """Admin password — full UI + every mutating endpoint. Set via
    HUB_AUTH; required."""

    auth_token_reader: str
    """Reader password — login succeeds with this string but the
    session is stamped as ``reader``, which means: (a) every mutating
    endpoint returns 403 (same gate as HUB_READONLY), and (b) the
    sidebar hides operator-only tabs (Stack, Settings, Quests,
    GrimoireLab Projects). Set via HUB_AUTH_READER; leave empty
    (default) to disable the reader role entirely — admin-only
    deploys keep their current behaviour."""

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

    public_knowledge: bool
    """When true, /hub/** is reachable without auth. Everything else
    (admin, databases, projects, …) keeps the single-password gate."""

    read_only: bool
    """When true, the hub refuses every mutating endpoint (stack up/down,
    container start/stop/restart, projects apply, pipeline run/stop,
    crawler job pause/resume/cancel/delete) with HTTP 403, and the
    sidebar drops the Stack + Settings tabs. Read-only views — services
    list, logs, dashboards, knowledge graph queries — keep working.

    Set ``HUB_READONLY=true`` in production deploys (e.g. openpulse.epfl.ch)
    so a leaked hub password can't be used to wreck the stack or apply
    a malicious projects.json. Operators still hold the CLI for any
    legitimate change."""

    qdrant_url: str
    """Base URL of the vector store. Defaults to the gme-qdrant sidecar
    so resolvers can read the per-provider collections (github_repos,
    zenodo_records, hf_*, ror_*, infoscience_*, …) GME already maintains."""

    qdrant_api_key: str
    """Optional bearer for Qdrant. Empty when running against the
    in-network gme-qdrant which has no auth."""

    llm_base_url: str
    """OpenAI-compatible chat-completions base URL. Works with OpenAI
    (api.openai.com/v1), OpenRouter, Ollama (localhost:11434/v1),
    EPFL RCP (inference-rcp.epfl.ch/v1), LM Studio, vLLM — anything
    that speaks the OpenAI schema.

    When ``HUB_LLM_*`` aren't set explicitly but ``RCP_TOKEN`` is
    present in the environment, we auto-target EPFL's inference
    endpoint with the same model the GME extractor uses
    (``openai/gpt-oss-120b``). That way deploying the hub on the
    EPFL stack lights up the narrative panel without per-key fiddling."""

    llm_api_key: str
    """API key for the chat endpoint. Falls back to ``RCP_TOKEN`` when
    ``HUB_LLM_API_KEY`` is empty (so the hub piggybacks on the
    extractor's existing EPFL inference credentials)."""

    llm_model: str
    """Model name passed to the chat endpoint."""


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
        auth_token_reader=os.environ.get("HUB_AUTH_READER", "").strip(),
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
            "HUB_ONTOLOGY_URL",
            "https://sdsc-ordes.github.io/open-pulse-ontology/versions/v2.1.2/index.html",
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
        public_knowledge=_env_bool("HUB_PUBLIC_KNOWLEDGE", default=False),
        read_only=_env_bool("HUB_READONLY", default=False),
        qdrant_url=os.environ.get("HUB_QDRANT_URL", "http://gme-qdrant:6333"),
        qdrant_api_key=os.environ.get("HUB_QDRANT_API_KEY", "").strip(),
        **_llm_settings(),
    )


# Default endpoint + model the extractor uses against EPFL's inference
# platform. Kept in sync with src/v1/llm/model_config.py inside the
# git-metadata-extractor image.
_RCP_BASE_URL = "https://inference-rcp.epfl.ch/v1"
_RCP_MODEL = "openai/gpt-oss-120b"


def _llm_settings() -> dict[str, str]:
    """Resolve llm_base_url / llm_api_key / llm_model with RCP fallback.

    Behavior:

    * Explicit ``HUB_LLM_*`` env vars always win.
    * If ``HUB_LLM_API_KEY`` is empty but ``RCP_TOKEN`` is set, the hub
      borrows the extractor's credentials and (unless overridden)
      points at EPFL's inference endpoint with the matching model.
    * Otherwise falls back to a generic OpenAI default so deployments
      outside EPFL still work with their own API key.
    """
    hub_key = os.environ.get("HUB_LLM_API_KEY", "").strip()
    hub_base = os.environ.get("HUB_LLM_BASE_URL", "").strip()
    hub_model = os.environ.get("HUB_LLM_MODEL", "").strip()
    rcp_token = os.environ.get("RCP_TOKEN", "").strip()

    using_rcp = not hub_key and bool(rcp_token)

    api_key = hub_key or rcp_token
    if hub_base:
        base_url = hub_base
    elif using_rcp:
        base_url = _RCP_BASE_URL
    else:
        base_url = "https://api.openai.com/v1"

    if hub_model:
        model = hub_model
    elif using_rcp:
        model = _RCP_MODEL
    else:
        model = "gpt-4o-mini"

    return {
        "llm_base_url": base_url.rstrip("/"),
        "llm_api_key": api_key,
        "llm_model": model,
    }
