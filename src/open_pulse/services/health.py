"""Service-level endpoint probes shared by CLI commands."""

from __future__ import annotations

import socket
from urllib.error import URLError
from urllib.request import Request, urlopen

from open_pulse.services.neo4j import Neo4jService
from open_pulse.services.tentris import TentrisService

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
    tentris: str,
    grimoirelab_db: str,
) -> list[tuple[str, str, bool, str]]:
    """Probe all known service endpoints and return results."""
    results: list[tuple[str, str, bool, str]] = []

    ok, detail = probe_http(neo4j_http)
    results.append(("Neo4j (HTTP)", neo4j_http, ok, detail))

    neo4j_service = Neo4jService(neo4j_bolt)
    ok, detail = neo4j_service.check_bolt()
    results.append(("Neo4j (Bolt)", neo4j_bolt, ok, detail))

    tentris_service = TentrisService(tentris)
    ok, detail = tentris_service.check_sparql()
    results.append(("Tentris (SPARQL)", tentris, ok, detail))

    db_host, db_port = parse_host_port(grimoirelab_db, 5432)
    ok, detail = probe_tcp(db_host, db_port)
    results.append(("GrimoireLab DB", grimoirelab_db, ok, detail))

    return results
