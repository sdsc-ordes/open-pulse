"""Per-host resolvers used by the hub knowledge surface.

Each module exposes a single ``resolve(ref: HubRef) -> Entity | None``
function. ``None`` signals "no data at all" so the route can queue the
URL into ``hub_wanted``; a populated Entity (even with ``enriched=False``)
means the resolver had at least some signal to surface.
"""
