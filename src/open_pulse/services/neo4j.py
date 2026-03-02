"""Neo4j service client abstractions."""

from __future__ import annotations

import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 5


class Neo4jService:
    """Lightweight Neo4j client wrapper."""

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
        self._closed = False

    def upload(self, context: dict[str, object]) -> None:
        """Placeholder upload entry point for pipeline data."""
        logger.info("neo4j service upload placeholder (endpoint=%s)", self.endpoint)

    def check_bolt(self) -> tuple[bool, str]:
        """Probe Neo4j Bolt endpoint reachability via TCP."""
        host, port = _parse_bolt_host_port(self.endpoint)
        try:
            with socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT):
                return True, "connection established"
        except OSError as exc:
            return False, str(exc)

    def close(self) -> None:
        """Release service resources."""
        self._closed = True


def _parse_bolt_host_port(endpoint: str) -> tuple[str, int]:
    parsed = urlparse(endpoint)
    host = parsed.hostname or "localhost"
    port = parsed.port or 7687
    return host, port
