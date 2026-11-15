"""Graph totals per library, and the coverage diagnostic.

Coverage is a DIAGNOSTIC ONLY: it reads `api_calls` of the dataset to see how many of the calls a problem makes
exist as Api nodes valid at that problem's version. It never decides what gets ingested.
"""

from __future__ import annotations

import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pinned.contract import (
    ADDED_IN, ALIAS_OF, API, CHANGED_IN, CUES, HAS_VERSION, MEMBER_OF, NEXT, REMOVED_IN, REPLACED_BY, VERSION,
)
from pinned.graph import HydraGraph

from .dataset import read_rows
from .deprecation import ABBREVIATIONS
from .validity import validity

API_EDGES = (ADDED_IN, REMOVED_IN, CHANGED_IN, REPLACED_BY, ALIAS_OF, MEMBER_OF)


CHUNK = 1000


def _count_in(g: HydraGraph, q: str, ids: list[int], workers: int = 8) -> int:
    chunks = [ids[i: i + CHUNK] for i in range(0, len(ids), CHUNK)]
    with ThreadPoolExecutor(workers) as pool:
        return sum(pool.map(lambda c: g.query(q, {"ids": c})[0]["n"], chunks))


def library_counts(g: HydraGraph, library: str, api_ids: list[int]) -> dict:
    """Node and edge counts of one library, read from the graph.

    HydraDB rejects a read that walks more than 250,000 edges (admission control `cypher_edge_rows`) and only the
    indexed equality on `library` narrows a scan (an id range does not), so a whole-library edge count fails for
    sympy / plotly. Edges are therefore counted per chunk of 1,000 Api ids (`WHERE a.id IN $ids`, an index lookup).
    `api_ids` = ids of every path the ladder's snapshots contain; `api_nodes` is the graph's own count, so a
    difference shows up as `api_nodes_not_enumerated`."""
    p = {"l": library}
    out = {"versions": g.query(f"MATCH (v:{VERSION}) WHERE v.library = $l RETURN count(*) AS n", p)[0]["n"],
           "api_nodes": g.query(f"MATCH (a:{API}) WHERE a.library = $l RETURN count(*) AS n", p)[0]["n"],
           "edges": {}}
    # no label in id-anchored reads: `MATCH (a:Api) WHERE a.id IN $ids` scans the label (~11 s per 1,000 ids on a
    # 136k-node graph) while `MATCH (a) WHERE a.id IN $ids` uses the id lookup (~0.1 s); ids already encode the label
    present = _count_in(g, "MATCH (a) WHERE a.id IN $ids RETURN count(*) AS n", api_ids)
    out["api_nodes_not_enumerated"] = out["api_nodes"] - present
    for rel in API_EDGES:
        out["edges"][rel] = _count_in(g, f"MATCH (a)-[:{rel}]->(b) WHERE a.id IN $ids RETURN count(*) AS n", api_ids)
    out["edges"][CUES] = _count_in(g, f"MATCH (t)-[:{CUES}]->(a) WHERE a.id IN $ids RETURN count(*) AS n", api_ids)
    out["edges"][NEXT] = g.query(f"MATCH (v:{VERSION})-[:{NEXT}]->(w) WHERE v.library = $l RETURN count(*) AS n",
                                 p)[0]["n"]
    out["edges"][HAS_VERSION] = out["versions"]
    return out


def coverage(g: HydraGraph, dataset: Path, import_names: dict[str, list[str]]) -> dict:
    """Share of each problem's own-library, import-rooted `api_calls` that are Api nodes valid at its version."""
    rows = read_rows(dataset, ("library", "version", "api_calls", "example_id"))
    per_lib: dict[str, dict] = {}
    for r in rows:
        lib = str(r["library"]).lower()
        names = import_names.get(lib, [])
        stat = per_lib.setdefault(lib, {"calls": 0, "valid": 0, "unscored_variable": 0, "unscored_other_library": 0,
                                        "status": defaultdict(int), "misses": []})
        calls = r.get("api_calls") or []
        own: list[tuple[str, str]] = []
        for call in calls:
            first, _, rest = call.partition(".")
            if first in ABBREVIATIONS and rest:
                full = f"{ABBREVIATIONS[first]}.{rest}"
                first = full.split(".", 1)[0]
                call_path = full
            else:
                call_path = call
            if first in names:
                own.append((call, call_path))
            elif any(first in v for v in import_names.values()):
                stat["unscored_other_library"] += 1
            else:
                stat["unscored_variable"] += 1
        if not own:
            continue
        res = validity(g, lib, [p for _, p in own], str(r["version"]))
        for call, p in own:
            stat["calls"] += 1
            st = res[p]
            stat["status"][st] += 1
            if st == "valid":
                stat["valid"] += 1
            else:
                stat["misses"].append({"example_id": r["example_id"], "version": r["version"], "call": call,
                                       "path": p, "status": st})
    total_calls = sum(s["calls"] for s in per_lib.values())
    total_valid = sum(s["valid"] for s in per_lib.values())
    for s in per_lib.values():
        s["share_valid"] = round(s["valid"] / s["calls"], 4) if s["calls"] else None
        s["status"] = dict(s["status"])
    return {"note": "diagnostic only; never used to decide what to ingest. Scored = calls rooted at the problem's "
                    "own library import name (np./pd./plt. abbreviations expanded); calls on variables "
                    "(e.g. df.append) and on other libraries are counted but not scored.",
            "total": {"calls": total_calls, "valid": total_valid,
                      "share_valid": round(total_valid / total_calls, 4) if total_calls else None},
            "libraries": dict(sorted(per_lib.items()))}


def write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1) + "\n")
