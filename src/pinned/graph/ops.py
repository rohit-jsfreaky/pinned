"""Graph helpers every part uses. Shapes chosen from what HydraDB v0.4.0 actually accepts (see docs/hydradb-findings.md).

Writes are idempotent (node batches MERGE by id; edges upsert on (src, rel, dst)), so any loader can be re-run safely.
- nodes:  `UNWIND $rows MERGE (n {id}) SET n:Label, n.key, n.<props>`   (batched, 1000 per request)
- edges without properties: `UNWIND $rows CREATE (a {id})-[:R]->(b {id})` (batched, dedups on src/dst/type)
- edges with properties: the batch above, then `UNWIND $rows MATCH (a {id})-[r:R]->(b {id}) SET r.p = row.p`
  (two requests per 1000 edges). A single `MERGE (a)-[:R {..}]->(b)` (upserts props in place) is for one-off edges.
Every node also stores `key` (its natural key), so `algo.MSpaths` can select nodes by key string.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from .client import HydraError, HydraGraph
from .ids import node_id

BATCH = 1000  # server cap is 1024 items per batch
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _name(s: str) -> str:
    """Labels, relationship types and property names are spliced into Cypher text: allow identifiers only."""
    if not _NAME.match(s):
        raise ValueError(f"not a safe Cypher identifier: {s!r}")
    return s


def _chunks(items: list, n: int = BATCH) -> Iterable[list]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


def _scalar(v: Any) -> bool:
    return v is None or isinstance(v, (str, int, float, bool))


def cypher_strings(values: Iterable[str]) -> str:
    """Inline a list of strings as a Cypher literal (procedure maps reject list parameters). Backslash before quote."""
    return "[" + ", ".join("'" + v.replace("\\", "\\\\").replace("'", "\\'") + "'" for v in values) + "]"


# ---------------------------------------------------------------------------------------------------- nodes
def upsert_nodes(g: HydraGraph, label: str, rows: list[dict]) -> list[int]:
    """Create or update nodes. Each row needs "key" (natural key) plus scalar props; returns ids in row order.

    Rows are grouped by their property-name set because every row of a batch must carry the same fields.
    None values are dropped (the server has no null literal in batches).
    """
    _name(label)
    ids: list[int] = []
    groups: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    for row in rows:
        if "key" not in row:
            raise ValueError("every node row needs a 'key'")
        props = {k: v for k, v in row.items() if v is not None}
        for k, v in props.items():
            _name(k)
            if not _scalar(v):
                raise ValueError(f"node property {k} must be a scalar, got {type(v).__name__}")
        props["id"] = node_id(label, str(row["key"]))
        ids.append(props["id"])
        groups[tuple(sorted(props))].append(props)
    for fields, items in groups.items():
        sets = ", ".join([f"n:{label}"] + [f"n.{f} = row.{f}" for f in fields if f != "id"])
        q = f"UNWIND $rows AS row MERGE (n {{id: row.id}}) SET {sets}"
        for chunk in _chunks(items):
            g.write(q, {"rows": chunk})
    return ids


def upsert_node(g: HydraGraph, label: str, key: str, **props: Any) -> int:
    return upsert_nodes(g, label, [{"key": key, **props}])[0]


def node(g: HydraGraph, label: str, node_id_: int, props: Iterable[str] = ("key",)) -> dict | None:
    """Read named props of one node (label-anchored so it never fans out over edges). None if absent."""
    cols = ", ".join(f"n.{_name(p)} AS {p}" for p in props)
    rows = g.query(f"MATCH (n:{_name(label)} {{id: $id}}) RETURN {cols}", {"id": node_id_})
    return rows[0] if rows else None


def delete_nodes(g: HydraGraph, ids: list[int]) -> None:
    for chunk in _chunks([{"id": i} for i in ids]):
        g.write("UNWIND $rows AS row MATCH (n {id: row.id}) DETACH DELETE n", {"rows": chunk})


# ---------------------------------------------------------------------------------------------------- edges
def link_many(g: HydraGraph, rel: str, pairs: Iterable[tuple[int, int]]) -> int:
    """Batch-create property-less edges (idempotent). Endpoints should already be labelled via upsert_nodes."""
    rows = [{"src": s, "dst": d} for s, d in dict.fromkeys(pairs)]
    q = f"UNWIND $rows AS row CREATE (a {{id: row.src}})-[:{_name(rel)}]->(b {{id: row.dst}})"
    for chunk in _chunks(rows):
        try:
            g.write(q, {"rows": chunk})
        except HydraError as e:
            if e.code != "internal":
                raise
            # HydraDB 0.4.0: a batch CREATE's idempotency key is `http-query-<counter>.unwind-create.<n>`; the counter
            # restarts at 0 when a node is started from a saved store, but the old keys are kept, so a new batch can
            # clash with one from before the restart ("idempotency key conflict ... different edge", reported to the
            # client as `internal`). UNWIND MERGE of edges is not supported, so fall back to one MERGE per edge
            # (idempotent by match, no counter key).
            for row in chunk:
                g.write(f"MERGE (a {{id: $src}})-[:{_name(rel)}]->(b {{id: $dst}})", row)
    return len(rows)


def link(g: HydraGraph, src: int, rel: str, dst: int, props: dict | None = None) -> None:
    """Create or update ONE edge with properties (MERGE upserts on src/rel/dst and overwrites the given props)."""
    props = {k: v for k, v in (props or {}).items() if v is not None}
    for k, v in props.items():
        _name(k)
        if not _scalar(v):
            raise ValueError(f"edge property {k} must be a scalar")
    pm = ", ".join(f"{k}: $p_{k}" for k in props)
    q = f"MERGE (a {{id: $src}})-[:{_name(rel)}{' {' + pm + '}' if pm else ''}]->(b {{id: $dst}})"
    g.write(q, {"src": src, "dst": dst, **{f"p_{k}": v for k, v in props.items()}})


def link_props_many(g: HydraGraph, rel: str, rows: list[tuple[int, int, dict]]) -> int:
    """Many edges with properties in two batched steps: create the edges (no props), then
    `UNWIND $rows MATCH (a)-[r]->(b) SET r.<p> = row.<p>` (~70x faster than one MERGE per edge; measured)."""
    link_many(g, rel, [(s, d) for s, d, _ in rows])
    set_edges_many(g, rel, rows)
    return len(rows)


def set_edges_many(g: HydraGraph, rel: str, rows: list[tuple[int, int, dict]]) -> int:
    """Batch-update properties of EXISTING edges (absent edges are skipped). Rows grouped by property-name set."""
    groups: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    for s, d, props in rows:
        props = {k: v for k, v in props.items() if v is not None}
        for k, v in props.items():
            _name(k)
            if not _scalar(v):
                raise ValueError(f"edge property {k} must be a scalar")
        if props:
            groups[tuple(sorted(props))].append({"s": s, "d": d, **props})
    for fields, items in groups.items():
        sets = ", ".join(f"r.{f} = row.{f}" for f in fields)
        q = f"UNWIND $rows AS row MATCH (a {{id: row.s}})-[r:{_name(rel)}]->(b {{id: row.d}}) SET {sets}"
        for chunk in _chunks(items):
            g.write(q, {"rows": chunk})
    return len(rows)


def set_edge(g: HydraGraph, src: int, rel: str, dst: int, props: dict) -> None:
    """Update properties of an existing edge in place (no-op when the edge is absent)."""
    sets = ", ".join(f"r.{_name(k)} = $p_{k}" for k in props)
    g.write(
        f"MATCH (a {{id: $src}})-[r:{_name(rel)}]->(b {{id: $dst}}) SET {sets}",
        {"src": src, "dst": dst, **{f"p_{k}": v for k, v in props.items()}},
    )


def unlink(g: HydraGraph, src: int, rel: str, dst: int) -> None:
    g.write(f"MATCH (a {{id: $src}})-[r:{_name(rel)}]->(b {{id: $dst}}) DELETE r", {"src": src, "dst": dst})


def edges_from(g: HydraGraph, ids: list[int], rel: str, props: Iterable[str] = (), dst_props: Iterable[str] = ("key",)) -> list[dict]:
    """Out-edges of many sources in one request: rows {src, dst, <rel props>, dst_<props>}."""
    cols = ["a.id AS src", "b.id AS dst"]
    cols += [f"r.{_name(p)} AS {p}" for p in props]
    cols += [f"b.{_name(p)} AS dst_{p}" for p in dst_props]
    q = f"MATCH (a)-[r:{_name(rel)}]->(b) WHERE a.id IN $ids RETURN {', '.join(cols)}"
    out: list[dict] = []
    for chunk in _chunks(list(ids)):
        out.extend(g.query(q, {"ids": chunk}))
    return out


# ---------------------------------------------------------------------------------------------------- counts
def count_nodes(g: HydraGraph, label: str) -> int:
    return g.query(f"MATCH (n:{_name(label)}) RETURN count(*) AS n")[0]["n"]


def count_edges(g: HydraGraph, src_label: str, rel: str) -> int:
    """Edges of type rel whose SOURCE carries src_label (unlabelled sources are invisible to label anchors)."""
    return g.query(f"MATCH (a:{_name(src_label)})-[:{_name(rel)}]->(b) RETURN count(*) AS n")[0]["n"]


# ---------------------------------------------------------------------------------------------------- paths
@dataclass
class Path:
    nodes: list[dict] = field(default_factory=list)  # {"id", "labels", **props}
    rels: list[dict] = field(default_factory=list)  # {"type", "src", "dst", **props}

    @property
    def start(self) -> dict:
        return self.nodes[0]

    @property
    def end(self) -> dict:
        return self.nodes[-1]

    @property
    def types(self) -> list[str]:
        return [r["type"] for r in self.rels]


def _untag(props: dict) -> dict:
    # path properties are tagged {"String": "x"} / {"Float": 1.0}, unlike every other cell
    return {k: next(iter(v.values())) if isinstance(v, dict) and len(v) == 1 else v for k, v in (props or {}).items()}


def parse_path(raw: dict) -> Path:
    return Path(
        nodes=[{"id": n["id"], "labels": n.get("labels", []), **_untag(n.get("properties"))} for n in raw.get("nodes", [])],
        rels=[
            {"type": r["edge_type"], "src": r["src"], "dst": r["dst"], **_untag(r.get("properties"))}
            for r in raw.get("relationships", [])
        ],
    )


def _rels(rels: Iterable[str]) -> str:
    return "[" + ", ".join(f"'{_name(r)}'" for r in rels) + "]"


def _dir(direction: str) -> str:
    if direction not in ("outgoing", "incoming", "both"):
        raise ValueError("direction must be outgoing, incoming or both")
    return direction


def paths(g: HydraGraph, src: int, dst: int, rels: Iterable[str], max_len: int = 4, count: int = 1,
          direction: str = "outgoing") -> list[Path]:
    """algo.SPpaths: bounded simple paths src -> dst. max_len counts EDGES; count 0 = all paths tied at the minimum."""
    q = (f"CALL algo.SPpaths({{sourceNode: $s, targetNode: $t, relTypes: {_rels(rels)}, relDirection: '{_dir(direction)}', "
         f"maxLen: $m, pathCount: $c}}) YIELD path RETURN path")
    return [parse_path(r["path"]) for r in g.query(q, {"s": src, "t": dst, "m": max_len, "c": count})]


def reach(g: HydraGraph, src: int, rels: Iterable[str], max_len: int = 3, limit: int = 1000,
          direction: str = "outgoing") -> list[Path]:
    """algo.SSpaths: every simple path from src up to max_len edges. `limit` caps the TOTAL paths returned
    (pathCount 0 would return only the shortest ones)."""
    q = (f"CALL algo.SSpaths({{sourceNode: $s, relTypes: {_rels(rels)}, relDirection: '{_dir(direction)}', "
         f"maxLen: $m, pathCount: $c}}) YIELD path RETURN path")
    return [parse_path(r["path"]) for r in g.query(q, {"s": src, "m": max_len, "c": limit})]


def multi_paths(g: HydraGraph, label: str, sources: list[str], targets: list[str], rels: Iterable[str],
                max_len: int = 3, count: int = 1, limit: int = 1000, prop: str = "key",
                direction: str = "outgoing") -> list[Path]:
    """algo.MSpaths: paths between many source and target nodes (selected by a string prop, default `key`) in ONE
    request. Pairs without a path are simply absent; join results back by endpoint key."""
    if not sources or not targets:
        return []
    q = (f"CALL algo.MSpaths({{sourceLabel: '{_name(label)}', sourceProperty: '{_name(prop)}', "
         f"sourceValues: {cypher_strings(sources)}, targetValues: {cypher_strings(targets)}, relTypes: {_rels(rels)}, "
         f"relDirection: '{_dir(direction)}', maxLen: $m, pathCount: $c, pairwise: false, resultLimit: $l}}) "
         "YIELD path RETURN path")
    return [parse_path(r["path"]) for r in g.query(q, {"m": max_len, "c": count, "l": limit})]
