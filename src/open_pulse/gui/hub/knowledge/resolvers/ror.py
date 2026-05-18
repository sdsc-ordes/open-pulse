"""ror.org resolver — research organizations.

ROR IDs are short opaque identifiers (e.g. ``02s6k3f65``). The
``ror_*`` collections are partitioned geographically; we fan out
across all of them and Qdrant returns whichever matches.
"""

from __future__ import annotations

from ..entity import Entity
from ..normalize import HubRef
from . import base

_COLLECTIONS = [
    "ror_epfl_ethz",
    "ror_switzerland",
    "ror_europe",
    "ror_worldwide",
]


def resolve(ref: HubRef, *, on_status=None) -> Entity | None:
    if not ref.path:
        return None
    ror_id = ref.path.split("/", 1)[0]
    if not ror_id:
        return None
    canonical = f"https://ror.org/{ror_id}"
    return base.build_entity(
        HubRef(host=ref.host, path=ror_id, canonical_url=canonical),
        collections=_COLLECTIONS,
        kind="Research organization (ROR)",
        title_fallback=f"ROR {ror_id}",
        on_status=on_status,
    )
