"""Service-level endpoint probes shared by CLI commands."""

from __future__ import annotations

import socket
from urllib.error import URLError
from urllib.request import Request, urlopen

from open_pulse.services.config import DEFAULT_CRAWLER_API_TOKEN_ENV
from open_pulse.services.crawler import CrawlerService
from open_pulse.services.neo4j import Neo4jService
from open_pulse.services.sparql_store import SparqlStoreService

_CONNECT_TIMEOUT = 5


def probe_http(url: str) -> tuple[bool, str]:
    """Try an HTTP GET against *url*. Returns ``(ok, detail)``."""
    try:
        req = Request(url, method="GET")
        with urlopen(req, timeout=_CONNECT_TIMEOUT) as resp:  # noqa: S310
            return True, f"HTTP {resp.status}"
    except URLError as exc:
        return False, str(getattr(exc, "reason", exc))
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def probe_tcp(host: str, port: int) -> tuple[bool, str]:
    """Try to open a TCP socket to *host*:*port*. Returns ``(ok, detail)``."""
    try:
        with socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT):
            return True, "connection established"
    except OSError as exc:
        return False, str(exc)


def parse_host_port(address: str, default_port: int) -> tuple[str, int]:
    """Split ``host:port`` with a fallback *default_port*."""
    if ":" in address:
        host, port_str = address.rsplit(":", 1)
        try:
            return host, int(port_str)
        except ValueError:
            pass
    return address, default_port


def probe_endpoints(
    neo4j_http: str,
    neo4j_bolt: str,
    sparql: str,
    grimoirelab_db: str,
    crawler: str,
) -> list[tuple[str, str, bool, str]]:
    """Probe all known service endpoints and return results."""
    results: list[tuple[str, str, bool, str]] = []

    ok, detail = probe_http(neo4j_http)
    results.append(("Neo4j (HTTP)", neo4j_http, ok, detail))

    neo4j_service = Neo4jService(neo4j_bolt)
    ok, detail = neo4j_service.check_bolt()
    results.append(("Neo4j (Bolt)", neo4j_bolt, ok, detail))

    sparql_service = SparqlStoreService(sparql)
    ok, detail = sparql_service.check_sparql()
    results.append(("SPARQL store", sparql, ok, detail))

    db_host, db_port = parse_host_port(grimoirelab_db, 5432)
    ok, detail = probe_tcp(db_host, db_port)
    results.append(("GrimoireLab DB", grimoirelab_db, ok, detail))

    # Crawler probe hits the unauthenticated /api/v1/health endpoint, so the
    # token-env name doesn't matter here — just pass the default for symmetry.
    crawler_service = CrawlerService(
        endpoint=crawler, api_token_env=DEFAULT_CRAWLER_API_TOKEN_ENV
    )
    try:
        ok, detail = crawler_service.check_health()
    finally:
        crawler_service.close()
    results.append(("Crawler API", f"{crawler.rstrip('/')}/api/v1/health", ok, detail))

    return results
