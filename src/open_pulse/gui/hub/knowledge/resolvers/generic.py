"""Fallback resolver for any host without a dedicated module.

Probes SPARQL, Neo4j, and every gme-qdrant collection the deployment
exposes using the canonical URL as the identity key. The collection
list mirrors the v1 host roster so unknown hosts can still surface
cross-collection backlinks (e.g. a ``doi.org`` URL referenced by a
Zenodo deposit).
"""

from __future__ import annotations

from ..entity import Entity
from ..normalize import HubRef
from . import base

_FALLBACK_COLLECTIONS = [
    "github_repos",
    "zenodo_records",
    "hf_models",
    "hf_datasets",
    "hf_spaces",
    "hf_orgs",
    "ror_epfl_ethz",
    "ror_switzerland",
    "ror_europe",
    "ror_worldwide",
    "infoscience_articles",
    "infoscience_persons",
    "infoscience_organizations",
    "renkulab_projects",
    "works",
]


def resolve(ref: HubRef, *, on_status=None) -> Entity | None:
    if not ref.is_known_host:
        return None
    return base.build_entity(
        ref,
        collections=_FALLBACK_COLLECTIONS,
        kind=f"Resource on {ref.host}",
        title_fallback=ref.display,
        enriched=False,
        on_status=on_status,
    )
