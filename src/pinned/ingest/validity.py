"""The contract's validity rule, read back from the graph (used by fill_gap and the coverage diagnostic)."""

from __future__ import annotations

from collections import defaultdict

from pinned.contract import ADDED_IN, REMOVED_IN
from pinned.graph import HydraGraph, edges_from, version_ord

from .writer import api_id


def validity(g: HydraGraph, library: str, paths: list[str], version: str) -> dict[str, str]:
    """valid | removed | added_later | missing: the latest ADDED_IN / REMOVED_IN at or before v decides."""
    ids = {api_id(library, p): p for p in paths}
    v = version_ord(version)
    events: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for rel, rank in ((REMOVED_IN, 0), (ADDED_IN, 1)):  # same ord: a re-add at that version wins
        for e in edges_from(g, list(ids), rel, dst_props=("ord",)):
            events[e["src"]].append((e["dst_ord"], rank))
    out = {}
    for i, p in ids.items():
        evs = sorted(events.get(i, []))
        upto = [e for e in evs if e[0] <= v]
        if upto:
            out[p] = "valid" if upto[-1][1] == 1 else "removed"
        elif any(rank == 1 for _, rank in evs):
            out[p] = "added_later"
        else:
            out[p] = "missing"
    return out
