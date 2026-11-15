"""Index-anchored edge reads.

HydraDB 0.4.0 plans an edge pattern filtered only by `WHERE a.id IN $list` as a full scan of that edge type (server log:
`access_path: FullEdgeScan:<REL>`; measured 55-75 ms at 60k CUES edges, growing with the node), while a `{id: $x}` map
anchor on one end uses the id index (~2 ms) even with `IN` on the other end. UNWIND cannot drive a read
(`composite parameter $rows is only supported as an UNWIND input`). So we send one small request per id on the
smaller side. Evidence: evidence/learn/hydradb-anchor-profile.json.
"""

from __future__ import annotations

from collections.abc import Iterable

from pinned.contract import Graph

CHUNK = 1000


def edges_between(g: Graph, rel: str, src_ids: Iterable[int], dst_ids: Iterable[int], cols: str) -> list[dict]:
    """Rows of `RETURN <cols>` (aliases a, r, b) for every `rel` edge from any src to any dst."""
    src, dst = list(dict.fromkeys(src_ids)), list(dict.fromkeys(dst_ids))
    if not src or not dst:
        return []
    if len(dst) <= len(src):
        anchor, others = dst, src
        q = f"MATCH (a)-[r:{rel}]->(b {{id: $one}}) WHERE a.id IN $many RETURN {cols}"
    else:
        anchor, others = src, dst
        q = f"MATCH (a {{id: $one}})-[r:{rel}]->(b) WHERE b.id IN $many RETURN {cols}"
    out: list[dict] = []
    for one in anchor:
        for i in range(0, len(others), CHUNK):
            out.extend(g.query(q, {"one": one, "many": others[i : i + CHUNK]}))
    return out


def edges_of_pairs(g: Graph, rel: str, pairs: Iterable[tuple[int, int]], cols: str) -> list[dict]:
    """Rows for exactly these (src, dst) pairs (cols must include `a.id AS src, b.id AS dst`)."""
    by_src: dict[int, list[int]] = {}
    for s, d in pairs:
        by_src.setdefault(s, []).append(d)
    want = {(s, d) for s, ds in by_src.items() for d in ds}
    out: list[dict] = []
    q = f"MATCH (a {{id: $one}})-[r:{rel}]->(b) WHERE b.id IN $many RETURN {cols}"
    for s, ds in by_src.items():
        for i in range(0, len(ds), CHUNK):
            out.extend(r for r in g.query(q, {"one": s, "many": ds[i : i + CHUNK]}) if (r["src"], r["dst"]) in want)
    return out


def edges_out(g: Graph, rel: str, src_ids: Iterable[int], cols: str) -> list[dict]:
    """Every `rel` edge out of each source (one map-anchored request per source)."""
    out: list[dict] = []
    for one in dict.fromkeys(src_ids):
        out.extend(g.query(f"MATCH (a {{id: $one}})-[r:{rel}]->(b) RETURN {cols}", {"one": one}))
    return out
