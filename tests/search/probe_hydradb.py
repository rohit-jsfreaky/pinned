"""Reproduce the HydraDB 0.4.0 behaviours search designs around, and save them (not a pytest test).

    uv run python -m tests.search.probe_hydradb --url http://127.0.0.1:28443

Writes evidence/search/hydradb-probes.json: expansion cost vs degree, LIMIT, typed expansion, Api-side reads, the
WHERE-order effect, and the exact errors for float sum() and bound variable-length relationships.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pinned.contract import API, CUES, SHORTCUT, TERM
from pinned.graph import HydraError, HydraGraph, link_props_many, upsert_nodes

OUT = Path(__file__).resolve().parents[2] / "evidence" / "search" / "hydradb-probes.json"


def timed(g: HydraGraph, q: str, p: dict, reps: int = 5) -> dict:
    ts, rows = [], []
    for _ in range(reps):
        t = time.perf_counter()
        rows = g.query(q, p)
        ts.append((time.perf_counter() - t) * 1000)
    return {"rows": len(rows), "median_ms": round(sorted(ts)[len(ts) // 2], 2)}


def error_of(g: HydraGraph, q: str, p: dict) -> str:
    try:
        g.query(q, p)
        return "no error"
    except HydraError as e:
        return f"{e.code}: {e.message}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("HYDRA_URL", "http://127.0.0.1:8443"))
    a = ap.parse_args()
    g = HydraGraph(url=a.url)
    run = "pr" + uuid.uuid4().hex[:6]
    degrees = [10, 100, 400, 1600]
    apis = upsert_nodes(g, API, [{"key": f"{run}|f{i}", "library": run, "path": f"m.f{i}", "kind": "function"}
                                 for i in range(max(degrees))])
    terms = upsert_nodes(g, TERM, [{"key": f"{run}t{d}", "word": f"{run}t{d}"} for d in degrees])
    for t, d in zip(terms, degrees):
        link_props_many(g, CUES, [(t, x, {"w": 1.0, "source": "doc"}) for x in apis[:d]])
    hub = terms[-1]
    link_props_many(g, SHORTCUT, [(hub, apis[0], {"w": 1.0, "lo_ord": 0, "hi_ord": 1, "hops": 2})])
    out: dict = {"utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "url": a.url, "run_prefix": run}

    exp = []
    for t, d in zip(terms, degrees):
        r = timed(g, f"MATCH (t:{TERM} {{id: $t}})-[r:{CUES}]->(a) RETURN count(*) AS n", {"t": t})
        exp.append({"degree": d, **r, "ms_per_edge": round(r["median_ms"] / d, 3)})
    out["expand_count_vs_degree"] = exp
    out["expand_hub_limit_5"] = timed(g, f"MATCH (t:{TERM} {{id: $t}})-[r:{CUES}]->(a) RETURN a.id AS a "
                                         "ORDER BY a ASC LIMIT 5", {"t": hub})
    out["hub_other_type_shortcut"] = timed(g, f"MATCH (t:{TERM} {{id: $t}})-[r:{SHORTCUT}]->(a) RETURN a.id AS a",
                                           {"t": hub})
    sample = apis[:30]
    out["cues_read_from_api_side_30_apis"] = timed(
        g, f"MATCH (t)-[r:{CUES}]->(a) WHERE a.id IN $apis AND t.id IN $terms RETURN a.id AS a, r.w AS w",
        {"apis": sample, "terms": [hub]})
    out["same_query_term_condition_first"] = timed(
        g, f"MATCH (t)-[r:{CUES}]->(a) WHERE t.id IN $terms AND a.id IN $apis RETURN a.id AS a, r.w AS w",
        {"apis": sample, "terms": [hub]})
    out["sum_of_float_property_error"] = error_of(
        g, f"MATCH (t {{id: $t}})-[r:{CUES}]->(a) RETURN sum(r.w) AS s", {"t": terms[0]})
    out["bound_variable_length_error"] = error_of(
        g, f"MATCH (a {{id: $s}})-[r:{CUES}*1..2]->(b) RETURN b.id AS b", {"s": hub})
    out["unbound_variable_length"] = error_of(
        g, f"MATCH (a {{id: $s}})-[:{CUES}*1..2]->(b) RETURN b.id AS b", {"s": hub})
    out["parameter_in_return_error"] = error_of(
        g, f"MATCH (a {{id: $s}})-[r:{CUES}]->(b) RETURN $x AS x", {"s": hub, "x": "y"})
    out["string_literal_in_return_with_union"] = error_of(
        g, f"MATCH (a {{id: $s}})-[r:{CUES}]->(b) RETURN b.id AS b, 'C' AS t UNION ALL "
           f"MATCH (a {{id: $s}})-[r:{SHORTCUT}]->(b) RETURN b.id AS b, 'S' AS t", {"s": hub})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=1))
    print("saved", OUT)


if __name__ == "__main__":
    main()
