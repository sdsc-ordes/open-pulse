"""Tentris service client abstractions."""

from __future__ import annotations

import logging
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 5


class TentrisService:
    """Lightweight Tentris client wrapper."""

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
        self._closed = False

    def upload(self, context: dict[str, object]) -> None:
        """Placeholder upload entry point for RDF payloads."""
        logger.info("tentris service upload placeholder (endpoint=%s)", self.endpoint)

    def check_sparql(self) -> tuple[bool, str]:
        """Probe Tentris SPARQL endpoint via HTTP GET."""
        try:
            req = Request(self.endpoint, method="GET")
            with urlopen(req, timeout=_CONNECT_TIMEOUT) as resp:  # noqa: S310
                return True, f"HTTP {resp.status}"
        except URLError as exc:
            return False, str(getattr(exc, "reason", exc))
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    def close(self) -> None:
        """Release service resources."""
        self._closed = True
