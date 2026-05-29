"""zenodo.org resolver — records, communities, depositors.

Zenodo URLs come in ``records/<id>`` (current) and ``record/<id>``
(older) forms; both resolve to the same record in our stores. The
qdrant payload keys off ``entity_id`` / ``zenodo_id`` (numeric), not
the URL.
"""

from __future__ import annotations

from ..entity import Entity, Fact
from ..normalize import HubRef
from . import base

_COLLECTIONS = ["zenodo_records"]


def resolve(ref: HubRef, *, on_status=None) -> Entity | None:
    parts = ref.path.split("/") if ref.path else []
    if not parts:
        return None

    head = parts[0].lower()
    if head in {"record", "records"} and len(parts) >= 2:
        record_id = parts[1]
        kind = "Zenodo record"
        title = f"zenodo:{record_id}"
        path = f"records/{record_id}"
    elif head == "communities" and len(parts) >= 2:
        kind = "Zenodo community"
        title = f"zenodo community: {parts[1]}"
        path = f"communities/{parts[1]}"
    else:
        kind = "Zenodo resource"
        title = ref.display
        path = ref.path

    canonical = f"https://zenodo.org/{path}"
    return base.build_entity(
        HubRef(host=ref.host, path=path, canonical_url=canonical),
        collections=_COLLECTIONS,
        kind=kind,
        title_fallback=title,
        identifiers_fn=_identifiers,
        on_status=on_status,
    )


def _identifiers(bindings: list[dict]) -> list[Fact]:
    wanted = {
        "http://schema.org/identifier": "identifier",
        "http://schema.org/sameAs": "sameAs",
        "http://purl.org/dc/terms/identifier": "dcterms:identifier",
    }
    out: list[Fact] = []
    for row in bindings:
        p = row.get("p", {}).get("value", "")
        if p not in wanted:
            continue
        v = row.get("o", {}).get("value", "")
        if not v:
            continue
        out.append(
            Fact(
                label=wanted[p],
                value=v,
                href=v if v.startswith("http") else "",
            )
        )
    return out
