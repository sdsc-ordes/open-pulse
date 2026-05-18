"""Canonical-URL normalisation for /hub/<ref:path>.

The hub treats the URL as the entity identifier, so two requests for
the same resource must converge on the same canonical form regardless
of how the visitor typed it:

* host lower-cased, ``www.`` prefix stripped
* trailing slash dropped
* query string and fragment ignored
* missing host means the ref was just a bare path (e.g. /hub/abc) →
  caller treats it as unknown

The same normalisation feeds the lookup key inside the wanted-list
and the Qdrant payload-filter, so consistency across surfaces is
critical.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True)
class HubRef:
    """A parsed /hub/<...> reference.

    ``host`` is the canonical lower-cased host (no ``www.``).
    ``path`` is the resource path with no leading or trailing slash.
    ``canonical_url`` is the ``https://<host>/<path>`` we use everywhere
    else as the identity key.
    """

    host: str
    path: str
    canonical_url: str

    @property
    def is_known_host(self) -> bool:
        return bool(self.host)

    @property
    def display(self) -> str:
        """Short label for headings: host/path with no scheme noise."""
        return f"{self.host}/{self.path}" if self.path else self.host


def _strip_www(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


def parse_ref(raw: str) -> HubRef:
    """Decode the /hub/{ref:path} capture into a HubRef.

    ``raw`` is whatever FastAPI's ``:path`` converter handed us, so it
    may already contain percent-encoded slashes or a leading scheme.
    We tolerate both ``github.com/sdsc-ordes/foo`` and
    ``https://github.com/sdsc-ordes/foo``.
    """
    s = unquote(raw or "").strip().strip("/")
    if not s:
        return HubRef(host="", path="", canonical_url="")

    # urlsplit drops the query/fragment naturally when a scheme is
    # present; for the bare-host branch we have to strip them ourselves.
    if "://" in s:
        parts = urlsplit(s)
        host = _strip_www(parts.netloc.lower())
        path = parts.path.strip("/")
    else:
        s_clean = s.split("?", 1)[0].split("#", 1)[0]
        head, sep, rest = s_clean.partition("/")
        host = _strip_www(head.lower())
        path = rest.strip("/") if sep else ""

    canonical_url = f"https://{host}/{path}" if path else f"https://{host}"
    return HubRef(host=host, path=path, canonical_url=canonical_url)


def canonicalise(url: str) -> str:
    """Public helper: take any URL and return its canonical form.

    Used by resolvers when matching a SPARQL ``?subject`` against
    "is this the page we're rendering?" — both sides are funnelled
    through this so a ``www.``/trailing-slash difference doesn't make
    them mismatch.
    """
    return parse_ref(url).canonical_url
