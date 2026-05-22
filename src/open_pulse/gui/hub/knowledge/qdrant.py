"""Search wrapper around ``gme-qdrant``.

The git-metadata-extractor's V2 RAG path already maintains one Qdrant
collection per provider — but the payload schemas differ across them.
A sample of what's actually stored:

* ``github_repos`` → ``entity_id`` / ``repo_id`` (the ``owner/repo``
  slug), ``owner``, ``name``, ``primary_language``, ``license_spdx``,
  ``stars``, ``forks``, ``is_archived``, ``pushed_at``. *No URL field.*
* ``zenodo_records`` → ``entity_id`` / ``zenodo_id`` (numeric record
  id), ``doi``, ``title``, ``year``, ``resource_type``. *No URL field.*
* ``ror_*`` → ``ror_id`` (the full ``https://ror.org/<id>``), ``name``,
  ``country_code``, ``types``, ``record``.
* ``infoscience_articles`` → ``article_uuid``, ``infoscience_url``,
  ``doi``, ``title``, ``authors``, ``keywords``, ``lab``.
* ``infoscience_persons`` → ``person_uuid``, ``profile_url``,
  ``family_name``, ``sciper_id``.
* ``hf_models`` → ``repo_id``, ``author``, ``license``, ``downloads``,
  ``pipeline_tag``.
* ``works`` (OpenAlex) → ``entity_id`` / ``openalex_id``
  (``https://openalex.org/W…``).

So URL-based payload filtering only works for ~half the collections.
For the rest we have to extract the host-specific identifier from the
canonical URL (``owner/repo`` from a github URL, ``<uuid>`` from an
infoscience path, etc.) and OR every candidate ``(field, value)`` pair
into one ``should`` filter. Qdrant silently ignores fields that aren't
indexed in the collection's schema, so the over-broad filter is safe.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import duckdb
import httpx

from ..auth import get_settings
from .entity import (
    BackLinkGroup,
    BackLinkItem,
    Fact,
    Mention,
    RelatedGroup,
    RelatedItem,
)
from .normalize import HubRef

log = logging.getLogger(__name__)

_QDRANT_TIMEOUT = 6.0

# Fields the renderer surfaces as facts when they appear in a payload.
# Anything not in this list is ignored to keep the page focused —
# ``chunk_index`` / ``chunk_count`` / ``entity_type`` are GME bookkeeping.
_FACT_FIELDS_ORDER = (
    "title",
    "name",
    "owner",
    "author",
    "doi",
    "ror_id",
    "openalex_id",
    "zenodo_id",
    "sciper_id",
    "primary_language",
    "license",
    "license_spdx",
    "year",
    "publication_date",
    "pushed_at",
    "last_modified",
    "stars",
    "forks",
    "downloads",
    "is_archived",
    "is_fork",
    "pipeline_tag",
    "library_name",
    "base_model",
    "country_code",
    "types",
    "resource_type",
    "access_right",
    "journal",
    "publication_type",
    "lab",
    "given_name",
    "family_name",
    "subjects",
    "keywords",
    "authors",
)

_FACT_SKIP = {
    # Text bodies — surfaced via the Mentions panel, not the Facts table.
    "text",
    "abstract",
    "summary",
    "description",
    # GME pipeline bookkeeping.
    "chunk_index",
    "chunk_count",
    "entity_type",
    "entity_id",
    "repo_id",
    "article_uuid",
    "person_uuid",
    "journal_uuid",
    "lab_uuid",
    "author_uuids",
    "related_article_uuids",
    "org_uuids",
    "record",  # ror dumps the entire upstream record here — too noisy
    # Internal "did the matcher find a backlink?" flags. Users don't
    # need to see ``has_github_match: no`` next to the title.
    "has_github_match",
    "has_hf_match",
    "matched_urls",  # tag list (e.g. ``['orcid']``), not real URLs
    # Self-references — the canonical URL already appears in the header.
    "infoscience_url",
    "profile_url",
    "homepage_url",
    "landing_page",
    "landingPage",
    "html_url",
    "url",
    "ror_id",
    "openalex_id",
}


# Values that signal "no data" / placeholder text. Treated as missing
# so we never render ``#PLACEHOLDER_PARENT_METADATA_VALUE#`` as a fact.
_PLACEHOLDER_VALUES = {
    "",
    "none",
    "null",
    "n/a",
    "-- undefined --",
    "#placeholder#",
}


def _is_placeholder(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        s = v.strip().lower()
        if s in _PLACEHOLDER_VALUES:
            return True
        if s.startswith("#placeholder") and s.endswith("#"):
            return True
    return False


_TEXT_FIELDS = ("text", "abstract", "summary", "description")


def _client() -> tuple[httpx.Client, dict[str, str]] | None:
    settings = get_settings()
    if not settings.qdrant_url:
        return None
    headers = {"Content-Type": "application/json"}
    if settings.qdrant_api_key:
        headers["api-key"] = settings.qdrant_api_key
    return httpx.Client(timeout=_QDRANT_TIMEOUT), headers


def _scroll(
    collection: str, payload_filter: dict[str, Any], limit: int
) -> list[dict[str, Any]]:
    pair = _client()
    if pair is None:
        return []
    client, headers = pair
    settings = get_settings()
    url = f"{settings.qdrant_url.rstrip('/')}/collections/{collection}/points/scroll"
    body = {
        "filter": payload_filter,
        "limit": int(limit),
        "with_payload": True,
        "with_vector": False,
    }
    try:
        r = client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        log.info("qdrant scroll %s failed: %s", collection, exc)
        return []
    finally:
        client.close()
    if r.status_code == 404:
        return []
    if r.status_code != 200:
        log.info("qdrant scroll %s HTTP %s", collection, r.status_code)
        return []
    try:
        body = r.json()
    except ValueError:
        return []
    return list((body.get("result") or {}).get("points") or [])


# ── Candidate-key derivation ──────────────────────────────────────────────


def _slug(ref: HubRef) -> str:
    return ref.path.strip("/")


def _candidate_keys(ref: HubRef) -> list[tuple[str, Any]]:
    """Per-host (payload-field, value) pairs to OR into the filter.

    Reading the candidates is the whole reason this resolver works:
    every host stores its identity in a different field. We try every
    plausible mapping; Qdrant silently drops the ones the collection
    doesn't index.
    """
    pairs: list[tuple[str, Any]] = []

    # Generic URL fields — work for ror_*, infoscience_*, works, openalex
    # and any future provider that records a primary URL.
    for f in (
        "url",
        "html_url",
        "iri",
        "id",
        "ror_id",
        "openalex_id",
        "infoscience_url",
        "profile_url",
        "landingPage",
        "doi_url",
    ):
        pairs.append((f, ref.canonical_url))

    slug = _slug(ref)
    parts = slug.split("/") if slug else []

    host = ref.host
    if host == "github.com" and len(parts) >= 2:
        full = f"{parts[0]}/{parts[1]}"
        pairs.append(("entity_id", full))
        pairs.append(("repo_id", full))
    elif host == "huggingface.co":
        head = parts[0].lower() if parts else ""
        if head in {"datasets", "spaces"} and len(parts) >= 3:
            full = f"{parts[1]}/{parts[2]}"
        elif len(parts) >= 2:
            full = f"{parts[0]}/{parts[1]}"
        else:
            full = parts[0] if parts else ""
        if full:
            pairs.append(("entity_id", full))
            pairs.append(("repo_id", full))
    elif (
        host == "zenodo.org"
        and len(parts) >= 2
        and parts[0].lower() in {"record", "records"}
    ):
        rec = parts[1]
        pairs.append(("entity_id", rec))
        pairs.append(("zenodo_id", rec))
        try:
            n = int(rec)
            pairs.append(("zenodo_id", n))
            pairs.append(("entity_id", n))
        except ValueError:
            pass
        pairs.append(("doi", f"10.5281/zenodo.{rec}"))
    elif host == "ror.org" and parts:
        pairs.append(("ror_id", ref.canonical_url))
        pairs.append(("id", parts[0]))
    elif host == "infoscience.epfl.ch":
        if len(parts) >= 3 and parts[0].lower() == "entities":
            uuid = parts[2]
            kind = parts[1].lower()
            if kind == "publication":
                pairs.append(("article_uuid", uuid))
            elif kind == "person":
                pairs.append(("person_uuid", uuid))
            elif kind == "organization":
                pairs.append(("org_uuid", uuid))

    return pairs


# ── Public surface ────────────────────────────────────────────────────────


def lookup_for_ref(
    collections: list[str], ref: HubRef, *, limit: int = 8
) -> tuple[list[Mention], list[Fact]]:
    """Hit every collection with all plausible (field, value) candidates.

    Returns ``(mentions, facts)``:
    * ``mentions`` are text-bearing chunks suitable for the agent /
      mentions panel.
    * ``facts`` are flattened payload key/value pairs ready for the
      facts table — this is the "retrieve all data available" path.
    """
    if not collections:
        return [], []

    candidates = _candidate_keys(ref)
    if not candidates:
        return [], []

    should = [{"key": f, "match": {"value": v}} for f, v in candidates]
    payload_filter = {"should": should}

    facts: list[Fact] = []
    seen_ids: set[tuple[str, str]] = set()
    # Group mentions by their *source*, not their chunk id. The GME
    # pipelines split each record into N text chunks (one Qdrant
    # point per chunk) — surfacing all of them produces a wall of
    # near-identical rows. We keep one per source, picking the chunk
    # with the most text so the snippet panel has real context.
    mentions_by_source: dict[tuple[str, str], Mention] = {}

    for collection in collections:
        points = _scroll(collection, payload_filter, limit)
        for p in points:
            point_id = str(p.get("id", ""))
            key = (collection, point_id)
            if key in seen_ids:
                continue
            seen_ids.add(key)
            payload = p.get("payload") or {}
            mention = _mention_from_payload(payload, collection)

            # Dedupe key: prefer the canonical URL, fall back through
            # progressively less-stable identifiers, finally the point id.
            source_key_str = (
                mention.source_url
                or payload.get("infoscience_url")
                or payload.get("profile_url")
                or payload.get("ror_id")
                or payload.get("openalex_id")
                or payload.get("repo_id")
                or payload.get("entity_id")
                or payload.get("article_uuid")
                or mention.source_label
                or point_id
            )
            source_key = (collection, str(source_key_str))
            existing = mentions_by_source.get(source_key)
            if existing is None or len(mention.text) > len(existing.text):
                mentions_by_source[source_key] = mention

            facts.extend(_facts_from_payload(payload, collection))

    # Drop mentions that ended up textless after dedupe — the Facts
    # table already carries the underlying payload, so an empty
    # "Mentions" card adds noise without information.
    mentions = [m for m in mentions_by_source.values() if m.text.strip()]
    return mentions, facts


def _mention_from_payload(payload: dict[str, Any], collection: str) -> Mention:
    text = ""
    for f in _TEXT_FIELDS:
        v = payload.get(f)
        if isinstance(v, str) and v.strip():
            text = v.strip()
            break
    label = ""
    for f in ("title", "name", "full_name", "display_name"):
        v = payload.get(f)
        if isinstance(v, str) and v.strip():
            label = v.strip()
            break
    source_url = ""
    for f in (
        "url",
        "html_url",
        "infoscience_url",
        "profile_url",
        "ror_id",
        "openalex_id",
    ):
        v = payload.get(f)
        if isinstance(v, str) and v.strip().startswith("http"):
            source_url = v.strip()
            break
    return Mention(
        text=text,
        source_url=source_url,
        source_label=label,
        collection=collection,
        score=0.0,
    )


def _dedupe_list(v: list[Any]) -> list[str]:
    """Order-preserving dedupe over a list, case-insensitive, skipping
    placeholder values."""
    seen: set[str] = set()
    out: list[str] = []
    for x in v:
        if _is_placeholder(x):
            continue
        s = str(x).strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


# Per-label "link this chip to another hub page" rules.
# Each entry maps a list-valued fact label to:
#   (sibling_uuid_field, hub_url_template_with_{uuid}).
# When the payload carries both fields, the resulting Fact gains
# value_links and the chips render as clickable hub links.
_FACT_CHIP_LINKERS: dict[str, tuple[str, str]] = {
    "authors": (
        "author_uuids",
        "/hub/infoscience.epfl.ch/entities/person/{uuid}",
    ),
    "org_names": (
        "org_uuids",
        "/hub/infoscience.epfl.ch/entities/organization/{uuid}",
    ),
}

# Single-valued labels that get linked when a sibling UUID field
# exists. ``lab → /hub/.../organization/<lab_uuid>``,
# ``journal → /hub/.../journal/<journal_uuid>``.
_FACT_SINGLE_LINKERS: dict[str, tuple[str, str]] = {
    "lab": (
        "lab_uuid",
        "/hub/infoscience.epfl.ch/entities/organization/{uuid}",
    ),
    "journal": (
        "journal_uuid",
        "/hub/infoscience.epfl.ch/entities/journal/{uuid}",
    ),
}


def _facts_from_payload(payload: dict[str, Any], collection: str) -> list[Fact]:
    """Flatten the payload into Facts.

    * Skips placeholder values (``#PLACEHOLDER_...#``, ``--``, empty
      strings) so the page doesn't surface "no data" markers as if
      they were real values.
    * Drops a fact whose rendered value matches one already added —
      Infoscience duplicates ``subjects`` and ``keywords`` with
      identical content, and we don't want both rows.
    * Multi-valued fields (lists) carry their items in
      ``Fact.value_list`` so the template can render them as chips.
    * When the payload carries a paired UUID field (e.g.
      ``authors`` + ``author_uuids``), the chips become clickable
      links to the corresponding hub pages — that's the graph-walk
      payoff: from a publication you hop to an author, from there to
      their other publications, etc.
    """
    out: list[Fact] = []
    seen_labels: set[str] = set()
    seen_renderings: set[str] = set()

    def _maybe_append(label: str, raw: Any) -> None:
        if _is_placeholder(raw):
            return
        items: tuple[str, ...] = ()
        value_links: tuple[tuple[str, str], ...] = ()
        if isinstance(raw, list):
            items = tuple(_dedupe_list(raw))
            if not items:
                return
            rendered = ", ".join(items[:8])
            if len(items) > 8:
                rendered += f", … (+{len(items) - 8} more)"
            # Pair with a sibling UUID list when configured.
            linker = _FACT_CHIP_LINKERS.get(label)
            if linker:
                uuid_field, template = linker
                raw_uuids = payload.get(uuid_field)
                if isinstance(raw_uuids, list) and len(raw_uuids) >= len(items):
                    links: list[tuple[str, str]] = []
                    for label_text, uid in zip(items, raw_uuids[: len(items)]):
                        if isinstance(uid, str) and uid.strip():
                            links.append((label_text, template.format(uuid=uid)))
                        else:
                            links.append((label_text, ""))
                    value_links = tuple(links)
        else:
            rendered = _render_value(raw)
            if not rendered or _is_placeholder(rendered):
                return
            # publication_type comes through as a Solr-style hierarchical
            # path (``text::journal::journal article::research article``).
            # The leaf segment is what most readers care about.
            if label == "publication_type" and "::" in rendered:
                rendered = rendered.rsplit("::", 1)[-1]
            # Pair with a single sibling UUID when configured.
            linker = _FACT_SINGLE_LINKERS.get(label)
            if linker:
                uuid_field, template = linker
                uid = payload.get(uuid_field)
                if isinstance(uid, str) and uid.strip():
                    value_links = ((rendered, template.format(uuid=uid)),)
        # Drop a duplicate of a value we already showed (Infoscience
        # repeats subjects/keywords with identical content).
        dedupe_key = rendered.lower()
        if dedupe_key in seen_renderings:
            return
        href = raw if isinstance(raw, str) and raw.startswith("http") else ""
        out.append(
            Fact(
                label=label,
                value=rendered,
                href=href,
                value_list=items,
                value_links=value_links,
            )
        )
        seen_labels.add(label)
        seen_renderings.add(dedupe_key)

    for f in _FACT_FIELDS_ORDER:
        if f in payload and f not in _FACT_SKIP:
            _maybe_append(f, payload[f])

    for k, v in payload.items():
        if k in seen_labels or k in _FACT_SKIP or k.startswith("_"):
            continue
        _maybe_append(k, v)

    return out


def _render_value(v: Any) -> str:
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, list):
        # Dedupe (Infoscience often duplicates entries across
        # ``subjects`` / ``keywords`` and within a single list) while
        # preserving order so the headline value stays first.
        seen: set[str] = set()
        parts: list[str] = []
        for x in v:
            if _is_placeholder(x):
                continue
            s = str(x).strip()
            if not s or s.lower() in seen:
                continue
            seen.add(s.lower())
            parts.append(s)
        if not parts:
            return ""
        if len(parts) > 8:
            return ", ".join(parts[:8]) + f", … (+{len(parts) - 8} more)"
        return ", ".join(parts)
    if isinstance(v, dict):
        keys = sorted(v.keys())
        if not keys:
            return ""
        if len(keys) > 6:
            return ", ".join(keys[:6]) + f", … (+{len(keys) - 6} more)"
        return ", ".join(keys)
    return str(v).strip()


# Back-compat: existing resolvers call ``search_by_url``. Keep it but
# route through the new lookup_for_ref so the broader matching applies.
def search_by_url(
    collections: list[str], canonical_url: str, *, limit: int = 8
) -> list[Mention]:
    """Legacy entry point — prefer :func:`lookup_for_ref`."""
    from .normalize import parse_ref

    ref = parse_ref(canonical_url)
    mentions, _ = lookup_for_ref(collections, ref, limit=limit)
    return mentions


# ── Collection summary surface ────────────────────────────────────────────


def list_collections() -> list[str]:
    """All collection names the configured Qdrant exposes."""
    pair = _client()
    if pair is None:
        return []
    client, headers = pair
    settings = get_settings()
    try:
        r = client.get(
            f"{settings.qdrant_url.rstrip('/')}/collections", headers=headers
        )
    except httpx.HTTPError as exc:
        log.info("qdrant collections list failed: %s", exc)
        return []
    finally:
        client.close()
    if r.status_code != 200:
        return []
    try:
        body = r.json()
    except ValueError:
        return []
    items = (body.get("result") or {}).get("collections") or []
    return [str(c.get("name")) for c in items if c.get("name")]


# Per-collection text-match fields for the hub home autocomplete.
# Each collection contributes up to 3 candidates; the home page
# merges them client-side. Qdrant silently ignores fields a
# collection doesn't index, so this list can stay broad.
_AUTOCOMPLETE_COLLECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("github_repos", ("name", "full_name", "owner", "repo_id")),
    ("hf_models", ("name", "author", "repo_id")),
    ("hf_datasets", ("name", "author", "repo_id")),
    ("hf_orgs", ("name", "login")),
    ("hf_spaces", ("name", "author", "repo_id")),
    ("zenodo_records", ("title", "doi")),
    ("infoscience_articles", ("title", "authors")),
    ("infoscience_persons", ("name", "family_name", "given_name")),
    ("infoscience_organizations", ("name",)),
    ("ror_worldwide", ("name",)),
    ("ror_switzerland", ("name",)),
    ("ror_epfl_ethz", ("name",)),
    ("works", ("title",)),
    ("authors", ("display_name", "name")),
    ("institutions", ("display_name",)),
    ("renkulab_projects", ("name", "slug", "namespace")),
    ("ethz_research_collection_articles", ("title",)),
    ("ethz_research_collection_persons", ("name",)),
)


def _autocomplete_one(
    collection: str, fields: tuple[str, ...], q: str, *, limit: int, timeout: float
) -> list[dict[str, Any]]:
    """Single-collection text-match scroll for autocomplete.

    Builds a should-OR of ``match.text`` clauses across the per-
    collection field list. Qdrant's text-match works on fields that
    were indexed with a full-text index — silently returns nothing
    on collections / fields that aren't.
    """
    should = [{"key": f, "match": {"text": q}} for f in fields]
    body = {
        "filter": {"should": should},
        "limit": limit,
        "with_payload": True,
        "with_vector": False,
    }
    pair = _client()
    if pair is None:
        return []
    _, headers = pair
    settings = get_settings()
    url = f"{settings.qdrant_url.rstrip('/')}/collections/{collection}/points/scroll"
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, json=body, headers=headers)
    except httpx.HTTPError:
        return []
    if r.status_code != 200:
        return []
    try:
        payload = r.json()
    except ValueError:
        return []
    return list((payload.get("result") or {}).get("points") or [])


def autocomplete(q: str, *, limit: int = 10) -> list[dict[str, str]]:
    """Suggest hub entities matching the user's typed query.

    Runs per-collection text-match scrolls in parallel and merges
    the results into a single ranked list. Per-collection limit 3,
    per-scroll timeout 2 s, total wall budget 4 s — autocomplete
    has to feel snappy.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time

    q = (q or "").strip()
    if len(q) < 2:
        return []

    deadline = time.monotonic() + 4.0
    per_call_timeout = 2.0
    out: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    def _probe(item: tuple[str, tuple[str, ...]]) -> list[dict[str, str]]:
        collection, fields = item
        points = _autocomplete_one(
            collection, fields, q, limit=3, timeout=per_call_timeout
        )
        rows: list[dict[str, str]] = []
        for p in points:
            payload = p.get("payload") or {}
            canonical = _canonical_url_for_point(collection, payload)
            if not canonical:
                continue
            stripped = canonical
            if stripped.startswith("https://"):
                stripped = stripped[len("https://") :]
            elif stripped.startswith("http://"):
                stripped = stripped[len("http://") :]
            if stripped.startswith("www."):
                stripped = stripped[len("www.") :]
            hub_url = f"/hub/{stripped.rstrip('/')}"
            label = _label_for_point(payload)
            badge = _badge_for_repo(payload)
            rows.append(
                {
                    "title": label,
                    "kind": collection,
                    "source_type": _source_type_for(collection),
                    "hub_url": hub_url,
                    "external_url": canonical,
                    "badge": badge,
                }
            )
        return rows

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_probe, item): item for item in _AUTOCOMPLETE_COLLECTIONS
        }
        for fut in as_completed(futures):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                for pending in futures:
                    pending.cancel()
                break
            try:
                rows = fut.result(timeout=remaining)
            except Exception:  # noqa: BLE001
                continue
            for row in rows:
                if not row["hub_url"] or row["hub_url"] in seen_urls:
                    continue
                seen_urls.add(row["hub_url"])
                out.append(row)
                if len(out) >= limit * 3:
                    break
            if len(out) >= limit * 3:
                break

    # Cheap relevance ranking: prefer hits whose title contains the
    # query as a substring; then alphabetise.
    qlow = q.lower()
    out.sort(
        key=lambda r: (
            0 if qlow in (r.get("title") or "").lower() else 1,
            r.get("title") or "",
        )
    )
    return out[:limit]


def sample_points(collection: str, *, limit: int = 12) -> list[dict[str, Any]]:
    """Pull a few representative points from a collection — used by
    the collection landing page (``/hub/c/<name>``) to surface
    "entry points" the visitor can click into.

    No filter; Qdrant returns whatever it has at the head of the
    storage. Cheap and useful for demoing a collection's shape.
    """
    pair = _client()
    if pair is None:
        return []
    client, headers = pair
    settings = get_settings()
    url = f"{settings.qdrant_url.rstrip('/')}/collections/{collection}/points/scroll"
    body = {"limit": int(limit), "with_payload": True, "with_vector": False}
    try:
        r = client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        log.info("qdrant sample %s failed: %s", collection, exc)
        return []
    finally:
        client.close()
    if r.status_code != 200:
        return []
    try:
        body = r.json()
    except ValueError:
        return []
    return list((body.get("result") or {}).get("points") or [])


def count_points(collection: str) -> int | None:
    """Fast point-count for a collection — None when unreachable."""
    pair = _client()
    if pair is None:
        return None
    client, headers = pair
    settings = get_settings()
    url = f"{settings.qdrant_url.rstrip('/')}/collections/{collection}/points/count"
    try:
        r = client.post(url, json={"exact": False}, headers=headers)
    except httpx.HTTPError as exc:
        log.info("qdrant count %s failed: %s", collection, exc)
        return None
    finally:
        client.close()
    if r.status_code != 200:
        return None
    try:
        body = r.json()
    except ValueError:
        return None
    result = body.get("result") or {}
    n = result.get("count")
    return int(n) if isinstance(n, int) else None


# ── Cross-collection backlinks ────────────────────────────────────────────

# Payload fields that across collections plausibly hold a cross-reference
# URL. We OR the entity's canonical URL against every one of them; Qdrant
# silently ignores fields the collection doesn't index.
#
# Curated for backlink performance: only fields that semantically point
# AT another resource (not the record's own primary identifier). Big
# OR-filters with 80 clauses slow Qdrant down to 17 s+; this list keeps
# the should-block tight so a sub-second scan is realistic when the
# index is warm.
_BACKLINK_URL_FIELDS = (
    "matched_urls",  # list of URLs the record matched (most reliable)
    "related_urls",  # list of related URLs
    "url",  # generic
    "html_url",  # GitHub-style
    "homepage",  # research orgs / projects
    "repository",  # publication / dataset → code link
    "repository_url",
    "code_repository",
)

# Short source-type tag shown alongside related/connected items so
# visitors can tell at a glance which upstream the chip is from.
# Keyed by collection name, falls back to "" (no tag rendered).
_SOURCE_TYPE_BY_COLLECTION: dict[str, str] = {
    "github_repos": "GitHub",
    "zenodo_records": "Zenodo",
    "hf_models": "HuggingFace",
    "hf_datasets": "HuggingFace",
    "hf_spaces": "HuggingFace",
    "hf_orgs": "HuggingFace",
    "ror_worldwide": "ROR",
    "ror_europe": "ROR",
    "ror_switzerland": "ROR",
    "ror_epfl_ethz": "ROR",
    "infoscience_articles": "Infoscience",
    "infoscience_persons": "Infoscience",
    "infoscience_organizations": "Infoscience",
    "infoscience_chunks": "Infoscience",
    "works": "OpenAlex",
    "authors": "OpenAlex",
    "institutions": "OpenAlex",
    "concepts": "OpenAlex",
    "topics": "OpenAlex",
    "sources": "OpenAlex",
    "renkulab_projects": "Renku",
    "renkulab_users": "Renku",
    "renkulab_groups": "Renku",
    "renkulab_data_connectors": "Renku",
    "orcid_epfl_persons": "ORCID",
    "orcid_epfl_employments": "ORCID",
    "orcid_epfl_educations": "ORCID",
    "orcid_switzerland_persons": "ORCID",
    "orcid_switzerland_employments": "ORCID",
    "snsf_epfl": "SNSF",
    "snsf_ethz": "SNSF",
    "snsf_switzerland": "SNSF",
    "ethz_research_collection_articles": "ETHZ research-collection",
    "ethz_research_collection_persons": "ETHZ research-collection",
    "ethz_research_collection_organizations": "ETHZ research-collection",
    "ethz_research_collection_chunks": "ETHZ research-collection",
    "swissubase_entities": "SWISSUbase",
    "epfl_graph_disciplines": "EPFL Graph",
}


def _source_type_for(collection: str) -> str:
    return _SOURCE_TYPE_BY_COLLECTION.get(collection, "")


# Display labels used in the panel header — keeps the hub vocabulary
# consistent across the home page and the entity page.
_BACKLINK_LABELS: dict[str, str] = {
    "github_repos": "GitHub repositories",
    "zenodo_records": "Zenodo records",
    "hf_models": "HuggingFace models",
    "hf_datasets": "HuggingFace datasets",
    "hf_spaces": "HuggingFace spaces",
    "hf_orgs": "HuggingFace organizations",
    "ror_worldwide": "ROR organizations (world)",
    "ror_europe": "ROR organizations (Europe)",
    "ror_switzerland": "ROR organizations (CH)",
    "ror_epfl_ethz": "ROR organizations (EPFL/ETHZ)",
    "infoscience_articles": "Infoscience publications",
    "infoscience_persons": "Infoscience persons",
    "infoscience_organizations": "Infoscience organizations",
    "infoscience_chunks": "Infoscience chunks",
    "works": "OpenAlex works",
    "authors": "OpenAlex authors",
    "institutions": "OpenAlex institutions",
    "concepts": "OpenAlex concepts",
    "topics": "OpenAlex topics",
    "sources": "OpenAlex sources",
    "renkulab_projects": "Renku projects",
    "renkulab_users": "Renku users",
    "renkulab_groups": "Renku groups",
}


def _canonical_url_for_point(collection: str, payload: dict[str, Any]) -> str:
    """Derive the public URL of a referencing record.

    Used to turn a backlink hit into a clickable /hub/<host>/<path>
    link. Falls back to the empty string if no URL can be deduced;
    such items just render as text.
    """
    # Direct URL fields the GME pipelines stash on most points.
    # ``source_url`` carries the canonical for swissubase records;
    # ``research_collection_url`` for ETHZ;
    # ``graphsearch_url`` for EPFL graph disciplines.
    for f in (
        "infoscience_url",
        "profile_url",
        "ror_id",
        "openalex_id",
        "html_url",
        "url",
        "homepage",
        "homepage_url",
        "landing_page",
        "landingPage",
        "research_collection_url",
        "graphsearch_url",
        "source_url",
        "feed_id",
    ):
        v = payload.get(f)
        if isinstance(v, str) and v.startswith("http"):
            return v

    # Slug-based collections — reconstruct the canonical URL.
    if collection == "github_repos":
        slug = payload.get("repo_id") or payload.get("entity_id")
        if isinstance(slug, str) and slug:
            return f"https://github.com/{slug}"
    if collection.startswith("hf_"):
        slug = payload.get("repo_id") or payload.get("entity_id")
        if isinstance(slug, str) and slug:
            return f"https://huggingface.co/{slug}"
    if collection == "zenodo_records":
        zid = payload.get("zenodo_id") or payload.get("entity_id")
        if zid not in (None, ""):
            return f"https://zenodo.org/records/{zid}"
    if collection == "renkulab_projects":
        # v2 routes are /v2/projects/<namespace>/<slug>; legacy paths
        # have /projects/<slug>. Prefer namespace+slug when both are
        # present.
        namespace = payload.get("namespace")
        slug = payload.get("slug") or payload.get("path")
        if isinstance(namespace, str) and namespace and isinstance(slug, str) and slug:
            return f"https://renkulab.io/v2/projects/{namespace}/{slug}"
        if isinstance(slug, str) and slug:
            return f"https://renkulab.io/projects/{slug}"
    if collection == "renkulab_users":
        slug = payload.get("slug") or payload.get("path") or payload.get("entity_id")
        if isinstance(slug, str) and slug:
            return f"https://renkulab.io/v2/users/{slug}"
    if collection == "renkulab_groups":
        slug = payload.get("slug") or payload.get("path") or payload.get("entity_id")
        if isinstance(slug, str) and slug:
            return f"https://renkulab.io/v2/groups/{slug}"
    if collection.startswith("orcid_"):
        # All orcid_* collections key off the bare ORCID identifier
        # (e.g. ``0000-0002-5899-551X``). Build the canonical URL.
        orcid = (
            payload.get("orcid_id") or payload.get("orcid") or payload.get("entity_id")
        )
        if isinstance(orcid, str) and orcid:
            return f"https://orcid.org/{orcid}"
    if collection.startswith("snsf_"):
        # SNSF grants — data portal at data.snf.ch indexes grants by
        # grant_number when present.
        grant = payload.get("grant_number") or payload.get("entity_id")
        if grant not in (None, ""):
            return f"https://data.snf.ch/grants/grant/{grant}"
    if collection.startswith("ethz_research_collection_"):
        # research-collection.ethz.ch entries are at
        # /entities/{publication|person|organization}/<uuid>.
        kind = collection.replace("ethz_research_collection_", "").rstrip("s")
        uuid = (
            payload.get(f"{kind}_uuid")
            or payload.get("article_uuid")
            or payload.get("person_uuid")
            or payload.get("entity_id")
        )
        if isinstance(uuid, str) and uuid:
            return f"https://www.research-collection.ethz.ch/entities/{kind}/{uuid}"
    if collection == "swissubase_entities":
        study = payload.get("study_id") or payload.get("entity_id")
        if study not in (None, ""):
            return f"https://www.swissubase.ch/en/catalogue/studies/{study}"
    if collection == "epfl_graph_disciplines":
        cat = payload.get("category_id") or payload.get("entity_id")
        if isinstance(cat, str) and cat:
            return f"https://graphsearch.epfl.ch/en/category/{cat}"

    # oamonitor_* collections (and a few others) stash the canonical URL
    # straight on ``entity_id`` — accept it when it's already an HTTP(S)
    # URL. Skipped above because that field is also used for non-URL
    # identifiers (slugs, ORCID, openalex IDs) which the collection-
    # specific branches above handle explicitly.
    eid = payload.get("entity_id")
    if isinstance(eid, str) and eid.startswith(("http://", "https://")):
        return eid

    return ""


def _label_for_point(payload: dict[str, Any]) -> str:
    """Human-friendly label for a backlink card."""
    for f in (
        "title",
        "name",
        "full_name",
        "display_name",
        "family_name",
    ):
        v = payload.get(f)
        if isinstance(v, str) and v.strip():
            return v.strip()
    # oamonitor_* points only carry ``embedding_text`` ("Title | DOI: …
    # | Year: …"). The first ``|``-separated segment is the title.
    et = payload.get("embedding_text")
    if isinstance(et, str) and et.strip():
        return et.split("|", 1)[0].strip() or et.strip()
    # Last-resort: any slug-shaped id.
    for f in ("repo_id", "entity_id", "openalex_id", "ror_id"):
        v = payload.get(f)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "(untitled)"


_BACKLINK_TIMEOUT = 8.0
"""Per-collection scroll timeout."""

_BACKLINK_WALL_BUDGET = 12.0
"""Hard cap on total wall time spent scanning backlinks. Whatever
finished is returned; the rest is dropped on the floor."""

_BACKLINK_WORKERS = 4
"""Parallel scrolls. Kept low because 16-way parallelism contends on
the local gme-qdrant and inverted the latency (first hit fast, the
next ones starve). 4 workers handle the 7 curated collections in
~two rounds with no contention."""

# Collections that actually carry cross-reference URL fields based on
# inspection of the gme-qdrant payloads. Scanning the full collection
# list (orcid_*, snsf_*, ethz_*, …) blows the wall budget without
# surfacing useful backlinks. ``infoscience_chunks`` is excluded
# despite being part of the infoscience domain — its rows are raw
# text fragments without URL fields, and it's consistently the
# slowest collection to scroll (>10 s on the local index).
_BACKLINK_TARGET_COLLECTIONS: tuple[str, ...] = (
    "infoscience_articles",
    "infoscience_persons",
    "infoscience_organizations",
    "works",
    "zenodo_records",
    "github_repos",
    "hf_models",
    "hf_datasets",
)


def _scroll_with_timeout(
    collection: str, payload_filter: dict[str, Any], limit: int, *, timeout: float
) -> list[dict[str, Any]]:
    """Variant of :func:`_scroll` with a caller-controlled timeout."""
    pair = _client()
    if pair is None:
        return []
    _, headers = pair  # we override the client to use a custom timeout below
    settings = get_settings()
    url = f"{settings.qdrant_url.rstrip('/')}/collections/{collection}/points/scroll"
    body = {
        "filter": payload_filter,
        "limit": int(limit),
        "with_payload": True,
        "with_vector": False,
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, json=body, headers=headers)
    except httpx.HTTPError:
        return []
    if r.status_code != 200:
        return []
    try:
        body = r.json()
    except ValueError:
        return []
    return list((body.get("result") or {}).get("points") or [])


def lookup_backlinks(
    ref: HubRef,
    *,
    exclude_collections: list[str] | None = None,
    per_collection_limit: int = 5,
    on_status: Any = None,
) -> list[BackLinkGroup]:
    """Scan every collection (except ``exclude_collections``) in
    parallel for points whose payload references ``ref``'s canonical
    URL.

    Each match becomes a :class:`BackLinkItem` that links to another
    /hub page so visitors can hop across the network.

    Two performance guards keep the page from stalling even when
    Qdrant is overloaded:

    * Each per-collection scroll has its own short timeout
      (:data:`_BACKLINK_TIMEOUT`).
    * The whole scan is also bounded by
      :data:`_BACKLINK_WALL_BUDGET`; collections that didn't return
      by then are quietly dropped.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    exclude = set(exclude_collections or [])
    canonical = ref.canonical_url

    # Just two aliases: canonical and trailing-slash variant. Adding
    # http:// + scheme-less variants triples filter size without
    # meaningfully more hits (records consistently store the https
    # form). Keep the should-block small or Qdrant gets very slow.
    aliases: list[str] = [canonical]
    if canonical and not canonical.endswith("/"):
        aliases.append(canonical + "/")

    should: list[dict[str, Any]] = []
    for field in _BACKLINK_URL_FIELDS:
        for alias in aliases:
            should.append({"key": field, "match": {"value": alias}})

    # Curate to the collections we've observed actually carry cross-
    # reference URLs in their payload. Scanning the whole space
    # (orcid_*, snsf_*, ethz_*, ror_*) generates a lot of work for
    # zero return on cross-reference lookups.
    available = set(list_collections())
    collections = [
        c for c in _BACKLINK_TARGET_COLLECTIONS if c in available and c not in exclude
    ]

    def _emit(msg: str) -> None:
        if on_status is None:
            return
        try:
            on_status(msg)
        except Exception:  # noqa: BLE001
            pass

    _emit(
        f"Scanning {len(collections)} other collections for backlinks "
        f"(parallel, {_BACKLINK_WALL_BUDGET:.0f}s budget)"
    )

    def _probe(collection: str) -> tuple[str, list[dict[str, Any]]]:
        return collection, _scroll_with_timeout(
            collection,
            {"should": should},
            per_collection_limit + 1,
            timeout=_BACKLINK_TIMEOUT,
        )

    deadline = time.monotonic() + _BACKLINK_WALL_BUDGET
    out: list[BackLinkGroup] = []
    timed_out: list[str] = []

    with ThreadPoolExecutor(max_workers=_BACKLINK_WORKERS) as pool:
        futures = {pool.submit(_probe, c): c for c in collections}
        for fut in as_completed(futures):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # Cancel everything still queued; in-flight requests
                # will end on their own timeout.
                for pending in futures:
                    pending.cancel()
                timed_out = [
                    c for f, c in futures.items() if not f.done() and not f.cancelled()
                ]
                break
            try:
                collection, points = fut.result(timeout=remaining)
            except Exception:  # noqa: BLE001
                continue
            if not points:
                continue

            items: list[BackLinkItem] = []
            for p in points[:per_collection_limit]:
                payload = p.get("payload") or {}
                label = _label_for_point(payload)
                external = _canonical_url_for_point(collection, payload)
                hub = ""
                if external:
                    stripped = external
                    if stripped.startswith("https://"):
                        stripped = stripped[len("https://") :]
                    elif stripped.startswith("http://"):
                        stripped = stripped[len("http://") :]
                    if stripped.startswith("www."):
                        stripped = stripped[len("www.") :]
                    hub = f"/hub/{stripped.rstrip('/')}"
                items.append(
                    BackLinkItem(label=label, hub_url=hub, external_url=external)
                )

            if items:
                out.append(
                    BackLinkGroup(
                        collection=collection,
                        label=_BACKLINK_LABELS.get(collection, collection),
                        items=items,
                        truncated=len(points) > per_collection_limit,
                    )
                )

    total = sum(len(g.items) for g in out)
    if timed_out:
        _emit(
            f"Backlinks: {total} refs across {len(out)} collections "
            f"(skipped {len(timed_out)} that exceeded the wall budget)"
        )
    else:
        _emit(f"Backlinks: {total} refs across {len(out)} collections")

    # Stable display order: most matches first, then collection name.
    out.sort(key=lambda g: (-len(g.items), g.collection))
    return out


# ── "Related" lookups ─────────────────────────────────────────────────────


def _related_primary_collection(host: str, path: str) -> str | None:
    """Which collection do we read the *entity's own* payload from?

    Used to pull the owner / language / etc. so we can ask Qdrant for
    siblings on the same axis.
    """
    if host == "github.com":
        return "github_repos"
    if host == "huggingface.co":
        parts = path.split("/") if path else []
        head = parts[0].lower() if parts else ""
        if head == "datasets":
            return "hf_datasets"
        if head == "spaces":
            return "hf_spaces"
        return "hf_models"
    if host == "zenodo.org":
        return "zenodo_records"
    if host == "ror.org":
        # Most ROR records live in the worldwide partition; if the
        # record isn't there we fall through and skip the panel.
        return "ror_worldwide"
    if host == "infoscience.epfl.ch":
        parts = path.split("/") if path else []
        if len(parts) >= 2 and parts[0].lower() == "entities":
            kind = parts[1].lower()
            if kind == "publication":
                return "infoscience_articles"
            if kind == "person":
                return "infoscience_persons"
            if kind == "organization":
                return "infoscience_organizations"
        return "infoscience_articles"
    return None


def _slug_for_point(collection: str, payload: dict[str, Any]) -> str | None:
    if collection == "github_repos":
        return payload.get("repo_id") or payload.get("entity_id")
    if collection.startswith("hf_"):
        return payload.get("repo_id") or payload.get("entity_id")
    if collection == "zenodo_records":
        return str(payload.get("zenodo_id") or payload.get("entity_id") or "")
    return None


def _badge_for_repo(payload: dict[str, Any]) -> str:
    """One-line meta shown under the related card."""
    parts: list[str] = []
    stars = payload.get("stars")
    if isinstance(stars, int) and stars > 0:
        parts.append(f"{stars:,}★")
    lang = payload.get("primary_language")
    if isinstance(lang, str) and lang and lang.lower() != "none":
        parts.append(lang)
    license_spdx = payload.get("license_spdx") or payload.get("license")
    if (
        isinstance(license_spdx, str)
        and license_spdx
        and license_spdx not in ("NOASSERTION", "None")
    ):
        parts.append(license_spdx)
    downloads = payload.get("downloads")
    if isinstance(downloads, int) and downloads > 0:
        parts.append(f"{downloads:,} downloads")
    pipeline = payload.get("pipeline_tag")
    if isinstance(pipeline, str) and pipeline and pipeline.lower() != "none":
        parts.append(pipeline)
    year = payload.get("year")
    if year:
        parts.append(str(year))
    country = payload.get("country_code")
    if isinstance(country, str) and country:
        parts.append(country.upper())
    return " · ".join(parts)


def _items_from_points(
    collection: str,
    host: str,
    points: list[dict[str, Any]],
    *,
    skip_slug: str | None = None,
) -> list[RelatedItem]:
    items: list[RelatedItem] = []
    seen: set[str] = set()
    source_type = _source_type_for(collection)
    for p in points:
        payload = p.get("payload") or {}
        slug = _slug_for_point(collection, payload)
        if slug and skip_slug and slug == skip_slug:
            continue
        if slug and slug in seen:
            continue
        if slug:
            seen.add(slug)
        external = _canonical_url_for_point(collection, payload)
        hub = ""
        if external:
            stripped = external
            if stripped.startswith("https://"):
                stripped = stripped[len("https://") :]
            elif stripped.startswith("http://"):
                stripped = stripped[len("http://") :]
            if stripped.startswith("www."):
                stripped = stripped[len("www.") :]
            hub = f"/hub/{stripped.rstrip('/')}"
        label = _label_for_point(payload)
        if collection == "github_repos" or collection.startswith("hf_"):
            # owner/repo slug is more informative than the bare name.
            label = slug or label
        items.append(
            RelatedItem(
                label=label,
                hub_url=hub,
                external_url=external,
                badge=_badge_for_repo(payload),
                source_type=source_type,
            )
        )
    return items


def lookup_related(ref: HubRef, *, per_group_limit: int = 6) -> list[RelatedGroup]:
    """Sibling entities — same owner / language / author / year /
    country / … — across both the entity's own collection AND other
    sources that share an axis (a GitHub owner often has a matching
    HuggingFace org, an HF author often has GitHub repos, etc.).

    Strategy: read the entity's own payload, then issue a small
    number of targeted ``must`` scrolls — some on the primary
    collection (siblings) and some on adjacent collections
    (cross-source). Dedupes across groups so the same item doesn't
    appear twice if it matches multiple axes.
    """
    collection = _related_primary_collection(ref.host, ref.path)
    if collection is None:
        return []

    candidates = _candidate_keys(ref)
    if not candidates:
        return []
    should = [{"key": f, "match": {"value": v}} for f, v in candidates]
    own_points = _scroll_with_timeout(
        collection, {"should": should}, 1, timeout=_BACKLINK_TIMEOUT
    )
    if not own_points:
        return []
    own_payload = own_points[0].get("payload") or {}
    own_slug = _slug_for_point(collection, own_payload)

    groups: list[RelatedGroup] = []
    surfaced: set[str] = {own_slug} if own_slug else set()

    def _add(
        filter_must: list[dict[str, Any]],
        title: str,
        *,
        target_collection: str | None = None,
        limit: int = per_group_limit,
    ) -> None:
        coll = target_collection or collection
        must_not: list[dict[str, Any]] = []
        if coll in ("github_repos",) or coll.startswith("hf_"):
            for skip in surfaced:
                if skip:
                    must_not.append({"key": "repo_id", "match": {"value": skip}})
                    must_not.append({"key": "entity_id", "match": {"value": skip}})
        f = {"must": filter_must}
        if must_not:
            f["must_not"] = must_not
        points = _scroll_with_timeout(coll, f, limit, timeout=_BACKLINK_TIMEOUT)
        items = _items_from_points(coll, ref.host, points, skip_slug=own_slug)
        if not items:
            return
        groups.append(RelatedGroup(title=title, items=items[:limit]))
        for it in items[:limit]:
            # Track slugs so a later group doesn't re-surface the
            # same item under a different reason.
            tail = it.hub_url.rsplit("/", 1)[-1] if "/" in it.hub_url else ""
            if tail:
                surfaced.add(tail)
            if "/" in it.hub_url:
                parts = it.hub_url.split("/", 2)
                if len(parts) == 3:
                    surfaced.add(parts[2])

    # ── Host-specific axes ───────────────────────────────────────
    if collection == "github_repos":
        owner = own_payload.get("owner")
        lang = own_payload.get("primary_language")
        license_spdx = own_payload.get("license_spdx")

        if owner:
            # Same-owner siblings on GitHub itself.
            _add(
                [{"key": "owner", "match": {"value": owner}}],
                f"More from {owner}",
            )
            # Cross-source: an HF organization or user with the same
            # handle — common for institutional accounts (e.g. an
            # ``sdsc-ordes`` GitHub org + ``sdsc-ordes`` HF org).
            _add(
                [{"key": "login", "match": {"value": owner}}],
                f"Also on HuggingFace as {owner}",
                target_collection="hf_orgs",
                limit=3,
            )
            _add(
                [{"key": "author", "match": {"value": owner}}],
                f"HuggingFace models from {owner}",
                target_collection="hf_models",
            )
            _add(
                [{"key": "author", "match": {"value": owner}}],
                f"HuggingFace datasets from {owner}",
                target_collection="hf_datasets",
            )
            _add(
                [{"key": "author", "match": {"value": owner}}],
                f"HuggingFace spaces from {owner}",
                target_collection="hf_spaces",
            )
            # Cross-source: OpenAlex works whose author institution
            # matches the org handle. ``works`` is large; cap tight.
            _add(
                [{"key": "host_venue", "match": {"value": owner}}],
                f"OpenAlex works from {owner}",
                target_collection="works",
                limit=3,
            )

        if lang and isinstance(lang, str) and lang.lower() not in ("", "none"):
            _add(
                [{"key": "primary_language", "match": {"value": lang}}],
                f"Other {lang} repositories",
            )
        if (
            license_spdx
            and isinstance(license_spdx, str)
            and license_spdx not in ("", "NOASSERTION", "None")
        ):
            _add(
                [{"key": "license_spdx", "match": {"value": license_spdx}}],
                f"Other {license_spdx} repositories",
            )

    elif collection.startswith("hf_"):
        author = own_payload.get("author")
        library = own_payload.get("library_name")
        pipeline = own_payload.get("pipeline_tag")

        if author:
            _add(
                [{"key": "author", "match": {"value": author}}],
                f"More from {author}",
            )
            # Cross-source: the same author on GitHub (often the
            # same login for institutional accounts).
            _add(
                [{"key": "owner", "match": {"value": author}}],
                f"GitHub repos from {author}",
                target_collection="github_repos",
            )
            # And on the other HF sub-collections.
            for other in ("hf_models", "hf_datasets", "hf_spaces", "hf_orgs"):
                if other == collection:
                    continue
                kind = other.replace("hf_", "")
                _add(
                    [{"key": "author", "match": {"value": author}}]
                    if other != "hf_orgs"
                    else [{"key": "login", "match": {"value": author}}],
                    f"HuggingFace {kind} from {author}",
                    target_collection=other,
                    limit=3,
                )

        if library and isinstance(library, str) and library.lower() != "none":
            _add(
                [{"key": "library_name", "match": {"value": library}}],
                f"Other {library} models",
            )
        if pipeline and isinstance(pipeline, str) and pipeline.lower() != "none":
            _add(
                [{"key": "pipeline_tag", "match": {"value": pipeline}}],
                f"Other {pipeline} models",
            )

    elif collection == "zenodo_records":
        year = own_payload.get("year")
        resource_type = own_payload.get("resource_type")
        doi = own_payload.get("doi")

        if year:
            _add([{"key": "year", "match": {"value": year}}], f"Other {year} deposits")
        if resource_type:
            _add(
                [{"key": "resource_type", "match": {"value": resource_type}}],
                f"Other {resource_type} deposits",
            )
        # Cross-source: any OpenAlex work that references this deposit's DOI.
        if doi and isinstance(doi, str):
            _add(
                [{"key": "doi", "match": {"value": doi}}],
                "OpenAlex citation for this DOI",
                target_collection="works",
                limit=3,
            )

    elif collection == "ror_worldwide":
        country = own_payload.get("country_code")
        name = own_payload.get("name")

        if country:
            _add(
                [{"key": "country_code", "match": {"value": country}}],
                f"Other organizations in {country}",
            )
        # Cross-source: institutions in OpenAlex with a matching name.
        if name and isinstance(name, str):
            _add(
                [{"key": "display_name", "match": {"value": name}}],
                f"OpenAlex institution: {name}",
                target_collection="institutions",
                limit=2,
            )

    elif collection.startswith("infoscience_"):
        # For publications, surface other works by the lab (cross-
        # publication via lab_uuid) and same-journal entries.
        lab_uuid = own_payload.get("lab_uuid")
        journal = own_payload.get("journal")
        year = own_payload.get("year")
        if lab_uuid:
            _add(
                [{"key": "lab_uuid", "match": {"value": lab_uuid}}],
                "More from the same lab",
                target_collection="infoscience_articles",
            )
        if journal and isinstance(journal, str):
            _add(
                [{"key": "journal", "match": {"value": journal}}],
                f"More in {journal}",
                target_collection="infoscience_articles",
            )
        if year:
            _add(
                [{"key": "year", "match": {"value": year}}],
                f"Other {year} EPFL publications",
                target_collection="infoscience_articles",
                limit=4,
            )

    return groups


def _github_contributors_from_duckdb(
    repo_id: str | None, limit: int
) -> list[dict[str, Any]]:
    """Read ``repos.contributors`` for ``owner/repo`` from the github
    DuckDB. Returns a list of ``{login, contributions}`` dicts, or an
    empty list when the file / row / column isn't available.

    The duckdb file location matches the ``_AUTO_TABLES`` mapping in
    ``duckdb_browser`` so we stay in sync with what the canvas's
    sibling features expect.
    """
    if not repo_id or "/" not in str(repo_id):
        return []
    db_path = (
        Path(os.environ.get("HUB_DATA_DIR_HOST", "/data"))
        / "extractor/index/github/duckdb/github.duckdb"
    )
    if not db_path.is_file():
        return []
    try:
        con = duckdb.connect(str(db_path), read_only=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("contributors duckdb open failed: %s", exc)
        return []
    try:
        try:
            row = con.execute(
                "SELECT contributors FROM repos WHERE repo_id = ? LIMIT 1",
                [repo_id],
            ).fetchone()
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("contributors query failed for %s: %s", repo_id, exc)
        return []
    if not row or row[0] is None:
        return []
    raw = row[0]
    if isinstance(raw, list):
        return raw[:limit]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed[:limit] if isinstance(parsed, list) else []
        except Exception:  # noqa: BLE001
            return []
    return []


def lookup_people(ref: HubRef, *, per_group_limit: int = 30) -> list[RelatedGroup]:
    """People-shaped relations for an entity: contributors of a repo,
    authors of a paper, owners of a dataset, etc.

    Each person becomes a :class:`RelatedItem` with a usable hub URL
    when one can be resolved (a GitHub login maps to
    ``/hub/github.com/<login>``); authors found only as free-form
    names get the same shape minus the URL so the canvas can still
    place them as labelled nodes the user can later wire up by hand.

    Read straight out of the entity's primary Qdrant payload — no
    extra parallel scrolls. Cheap to call from the expand endpoint
    even when the entity has dozens of authors.
    """
    collection = _related_primary_collection(ref.host, ref.path)
    if collection is None:
        return []

    candidates = _candidate_keys(ref)
    if not candidates:
        return []
    should = [{"key": f, "match": {"value": v}} for f, v in candidates]
    own = _scroll_with_timeout(
        collection, {"should": should}, 1, timeout=_BACKLINK_TIMEOUT
    )
    if not own:
        return []
    payload: dict[str, Any] = own[0].get("payload") or {}

    def _maybe_load(v: Any) -> list[Any]:
        # Some duckdb-derived fields land here as JSON-encoded strings.
        if isinstance(v, list):
            return v
        if isinstance(v, str) and v.startswith("[") and v.endswith("]"):
            try:
                parsed = json.loads(v)
                return parsed if isinstance(parsed, list) else []
            except Exception:  # noqa: BLE001
                return []
        return []

    groups: list[RelatedGroup] = []

    # GitHub repo → contributors. The Qdrant payload only carries the
    # repo metadata used for embedding; the per-contributor list lives
    # in the source-of-truth DuckDB. Fall back there when the payload
    # doesn't already include it.
    if collection == "github_repos":
        contribs = _maybe_load(payload.get("contributors"))
        if not contribs:
            contribs = _github_contributors_from_duckdb(
                payload.get("repo_id"), per_group_limit
            )
        items: list[RelatedItem] = []
        for c in contribs[:per_group_limit]:
            if isinstance(c, dict):
                login = (c.get("login") or "").strip()
                count = c.get("contributions")
            else:
                login = str(c).strip()
                count = None
            if not login:
                continue
            badge = f"{count} commits" if isinstance(count, int) else ""
            items.append(
                RelatedItem(
                    label=login,
                    hub_url=f"/hub/github.com/{login}",
                    external_url=f"https://github.com/{login}",
                    badge=badge,
                    source_type="GitHub user",
                )
            )
        if items:
            groups.append(RelatedGroup(title="Contributors", items=items))

    # Publication-shaped collections → authors. Schemas vary slightly:
    # infoscience stores a flat ``authors`` list; OpenAlex ``works``
    # carries ``author_names`` and ``authorships``; ETHZ has authors
    # too. We accept either form.
    if (
        collection.startswith("infoscience_")
        or collection == "works"
        or collection.startswith("ethz_research_collection_")
        or collection == "oamonitor_publications"
        or collection == "zenodo_records"
    ):
        raw_authors = (
            _maybe_load(payload.get("authors"))
            or _maybe_load(payload.get("author_names"))
            or _maybe_load(payload.get("creators"))
            or _maybe_load(payload.get("authorships"))
            or []
        )
        seen: set[str] = set()
        items: list[RelatedItem] = []
        for a in raw_authors[:per_group_limit]:
            if isinstance(a, dict):
                label = (
                    a.get("display_name")
                    or a.get("name")
                    or a.get("full_name")
                    or a.get("author_name")
                    or ""
                ).strip()
                orcid = (a.get("orcid") or "").strip()
                if orcid and orcid.startswith("http"):
                    hub_url = "/hub/" + orcid.replace("https://", "").replace(
                        "http://", ""
                    )
                else:
                    hub_url = ""
            else:
                label = str(a).strip()
                hub_url = ""
            if not label or label in seen:
                continue
            seen.add(label)
            items.append(
                RelatedItem(
                    label=label,
                    hub_url=hub_url,
                    external_url="",
                    badge="",
                    source_type="Author",
                )
            )
        if items:
            groups.append(RelatedGroup(title="Authors", items=items))

    return groups


# ── TTL cache for the lazy panels ────────────────────────────────────────

_PANEL_CACHE: dict[tuple[str, str], tuple[float, Any]] = {}
_PANEL_CACHE_TTL = 300.0
_PANEL_CACHE_MAX = 256


# ── Connected GitHub graph for non-github entities ───────────────────────


# Payload fields that across the non-github collections might carry a
# GitHub URL pointing FROM the current entity. ``matched_urls`` is the
# Infoscience-style match log; ``url`` / ``homepage`` / ``repository``
# are common across OpenAlex / Zenodo / ROR / HuggingFace.
_GITHUB_LINK_FIELDS = (
    "matched_urls",
    "related_urls",
    "url",
    "homepage",
    "homepage_url",
    "repository",
    "repository_url",
    "code_repository",
)


def _github_slug_from_url(u: str) -> str:
    """Extract ``owner/repo`` from a github URL — empty if not a repo URL."""
    if not isinstance(u, str):
        return ""
    s = u.strip()
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if s.startswith(prefix):
            s = s[len(prefix) :]
            break
    else:
        return ""
    s = s.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    parts = s.split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return ""
    # Reject in-repo paths (issues, pull, blob, tree, …) — we want the
    # repository landing, not a sub-resource.
    if parts[1] in {"issues", "pull", "blob", "tree", "actions", "releases", "wiki"}:
        return ""
    return f"{parts[0]}/{parts[1]}"


def _collect_github_links(payload: dict[str, Any]) -> set[str]:
    """Pull every github owner/repo slug out of a Qdrant payload."""
    out: set[str] = set()
    for f in _GITHUB_LINK_FIELDS:
        v = payload.get(f)
        if isinstance(v, str):
            slug = _github_slug_from_url(v)
            if slug:
                out.add(slug)
        elif isinstance(v, list):
            for u in v:
                slug = _github_slug_from_url(u if isinstance(u, str) else "")
                if slug:
                    out.add(slug)
    return out


def lookup_connected_github(ref: HubRef, *, limit: int = 10) -> list[RelatedItem]:
    """GitHub repos referenced FROM a non-github entity's Qdrant payload.

    For an Infoscience publication that lists GitHub repos in its
    ``matched_urls``, this surfaces each repo as a clickable card with
    its stars / language / license badge so the visitor can hop into
    the GitHub-side of the graph (where Neo4j and OpenSearch have
    richer data).

    Returns an empty list when:
    * The ref *is* a github URL (caller already shows neighbours from
      github_repos).
    * The entity isn't in any Qdrant collection.
    * No github URL appears in the payload.
    """
    if ref.host == "github.com" or not ref.is_known_host:
        return []

    collection = _related_primary_collection(ref.host, ref.path)
    if collection is None:
        # Infoscience / others not covered by the primary-collection map
        # — fall through and let the caller decide which collection to
        # peek into via lookup_for_ref.
        return _lookup_connected_github_via_lookup(ref, limit=limit)

    candidates = _candidate_keys(ref)
    if not candidates:
        return []
    should = [{"key": f, "match": {"value": v}} for f, v in candidates]
    # Pull a handful of chunks because GME splits long records and
    # `matched_urls` may sit on chunk #0 while we get chunk #3 first.
    points = _scroll_with_timeout(
        collection, {"should": should}, 5, timeout=_BACKLINK_TIMEOUT
    )
    if not points:
        return _lookup_connected_github_via_lookup(ref, limit=limit)

    slugs: set[str] = set()
    for p in points:
        slugs |= _collect_github_links(p.get("payload") or {})
    return _items_for_github_slugs(sorted(slugs), limit=limit)


def _lookup_connected_github_via_lookup(
    ref: HubRef, *, limit: int
) -> list[RelatedItem]:
    """Fallback when we don't have a primary collection registered:
    rescan all infoscience_* style collections via lookup_for_ref and
    drain github links from whatever the entity's chunks return."""
    # Infoscience splits content across infoscience_articles +
    # infoscience_chunks; ``lookup_for_ref`` already knows the key
    # candidates per host.
    mentions, _facts = lookup_for_ref(
        [
            "infoscience_articles",
            "infoscience_persons",
            "infoscience_organizations",
            "zenodo_records",
            "hf_models",
            "hf_datasets",
            "works",
            "ror_worldwide",
        ],
        ref,
        limit=5,
    )
    # `lookup_for_ref` doesn't return raw payloads — we'd need a
    # separate pass to extract URLs. The mentions object only carries
    # source_url. Skip for now; the primary-collection path covers all
    # supported hosts already.
    _ = mentions
    return []


def _items_for_github_slugs(slugs: list[str], *, limit: int) -> list[RelatedItem]:
    """Resolve each ``owner/repo`` slug to a RelatedItem, decorated
    with the github_repos payload (stars / language / license) when
    available AND with Neo4j community stats (contributors / owning
    org) so the visitor can see graph context without leaving the
    panel.
    """
    from .stores import neo4j_repo_stats

    bounded = slugs[:limit]
    stats = neo4j_repo_stats(bounded)
    items: list[RelatedItem] = []
    for slug in bounded:
        gh_points = _scroll_with_timeout(
            "github_repos",
            {
                "should": [
                    {"key": "entity_id", "match": {"value": slug}},
                    {"key": "repo_id", "match": {"value": slug}},
                ]
            },
            1,
            timeout=_BACKLINK_TIMEOUT,
        )
        gh_payload = gh_points[0].get("payload") or {} if gh_points else {}

        parts: list[str] = []
        b = _badge_for_repo(gh_payload)
        if b:
            parts.append(b)
        s = stats.get(slug, {})
        if s.get("contributors"):
            parts.append(f"{s['contributors']} contributors")
        if s.get("owner_login"):
            parts.append(f"owned by {s['owner_login']}")
        if not parts and not s.get("indexed"):
            parts.append("not yet indexed")
        badge = " · ".join(parts)

        items.append(
            RelatedItem(
                label=slug,
                hub_url=f"/hub/github.com/{slug}",
                external_url=f"https://github.com/{slug}",
                badge=badge,
            )
        )
    return items


def cached_panel(name: str, canonical_url: str, fn: callable) -> Any:
    """Memoise a lazy-panel computation by (name, canonical_url).

    The cache lives in the hub process — fine since the data plane is
    visibly slow and these are read-only lookups. Entries expire after
    ``_PANEL_CACHE_TTL`` seconds so backlinks can pick up newly-added
    references on the next refresh.
    """
    import time

    key = (name, canonical_url)
    now = time.monotonic()
    hit = _PANEL_CACHE.get(key)
    if hit is not None and (now - hit[0]) < _PANEL_CACHE_TTL:
        return hit[1]
    value = fn()
    # Drop arbitrary entries when bounded.
    if len(_PANEL_CACHE) >= _PANEL_CACHE_MAX:
        for k in list(_PANEL_CACHE)[: _PANEL_CACHE_MAX // 2]:
            _PANEL_CACHE.pop(k, None)
    _PANEL_CACHE[key] = (now, value)
    return value
