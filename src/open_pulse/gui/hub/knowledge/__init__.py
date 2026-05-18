"""Open Pulse Hub — knowledge surface.

URL-as-identifier pages: ``/hub/<host>/<path>`` renders everything the
stack knows about a single resource (GitHub repo, Zenodo record, ROR
org, …) by stitching together:

* SPARQL facts from Oxigraph (schema.org-typed metadata extracted by
  the GME service).
* 1-hop graph neighbours from Neo4j (the crawl graph).
* Pre-computed semantic chunks from the gme-qdrant collections that
  the GME V2 RAG path already maintains
  (``github_repos``, ``zenodo_records``, ``hf_*``, ``ror_*``,
  ``infoscience_*``, …).
* An optional written narrative produced by an OpenAI-compatible chat
  endpoint (the "agent" layer; degrades to no-op when unconfigured).

The module sits inside the hub package and is invoked from
``routes/hub.py`` — see :func:`open_pulse.gui.hub.knowledge.registry.resolve`.
"""

from __future__ import annotations

from .entity import Entity, Fact, Mention, Neighbour
from .normalize import HubRef, canonicalise, parse_ref
from .registry import resolve

__all__ = [
    "Entity",
    "Fact",
    "HubRef",
    "Mention",
    "Neighbour",
    "canonicalise",
    "parse_ref",
    "resolve",
]
