"""Profile the read shapes maintain() / PlainStore.from_graph use, on a node holding the scale-run graph.

    uv run python tests/learn/scan_profile.py --url http://127.0.0.1:28443 --library <scale library> --container pinned-d-scale

Writes evidence/learn/hydradb-anchor-profile.json. Shows that a library-filtered edge read costs a scan of every edge of
that type (HydraDB logs `FullEdgeScan` for it), that id-IN chunks are not cheaper, and that a `{id: $x}` map anchor on
one end of a small edge read is what avoids the scan.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from pinned.graph import HydraGraph

OUT = Path(__file__).resolve().parents[2] / "evidence" / "learn" / "hydradb-anchor-profile.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--library", required=True)
    ap.add_argument("--container", default="", help="docker container name, to count the server's plan warnings")
    a = ap.parse_args()
    g = HydraGraph(url=a.url)
    lib = {"lib": a.library}
    res: dict = {"utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "url": a.url, "library": a.library,
                 "queries": []}

    def timed(name: str, fn) -> list:
        t = time.perf_counter()
        rows = fn()
        res["queries"].append({"name": name, "seconds": round(time.perf_counter() - t, 3), "rows": len(rows)})
        return rows

    timed("ADDED_IN of one library, filtered on a.library",
          lambda: g.query("MATCH (a:Api)-[r:ADDED_IN]->(v) WHERE a.library = $lib RETURN a.id AS id, r.sig AS sig, "
                          "v.ord AS ord", lib))
    timed("ADDED_IN of every library, unfiltered",
          lambda: g.query("MATCH (a:Api)-[r:ADDED_IN]->(v) RETURN a.id AS id, r.sig AS sig, v.ord AS ord"))
    ids = [r["id"] for r in timed("Api ids of one library",
                                   lambda: g.query("MATCH (a:Api) WHERE a.library = $lib RETURN a.id AS id", lib))]

    def chunked() -> list:
        out: list = []
        for i in range(0, len(ids), 1000):
            out += g.query("MATCH (a)-[r:ADDED_IN]->(v) WHERE a.id IN $ids RETURN a.id AS id, r.sig AS sig, "
                           "v.ord AS ord", {"ids": ids[i : i + 1000]})
        return out

    timed("ADDED_IN of the same Api ids, id-anchored chunks of 1000", chunked)
    timed("CUES into one library (destination label + library filter)",
          lambda: g.query("MATCH (t)-[r:CUES]->(a:Api) WHERE a.library = $lib RETURN t.word AS w, a.path AS p, "
                          "r.w AS x", lib))
    timed("decay scan: CUES with n_fail >= 3 (whole node)",
          lambda: g.query("MATCH (a:Term)-[r:CUES]->(b) WHERE r.n_fail >= 3 RETURN a.id AS s"))
    timed("SHORTCUT edges (small type)", lambda: g.query("MATCH (a:Term)-[r:SHORTCUT]->(b) RETURN a.id AS s"))
    # anchor shapes for small edge reads (what record() does per episode)
    words = [r["w"] for r in g.query("MATCH (t)-[r:CUES]->(a:Api) WHERE a.library = $lib RETURN t.id AS w", lib)[:10]]
    dst = [r["d"] for r in g.query("MATCH (a {id: $s})-[r:CUES]->(b) RETURN b.id AS d", {"s": words[0]})][:3]
    shapes = {
        "edge read, a.id IN $list (10 ids)": ("MATCH (a)-[r:CUES]->(b) WHERE a.id IN $s RETURN b.id AS d", {"s": words}),
        "edge read, a.id IN and b.id IN": ("MATCH (a)-[r:CUES]->(b) WHERE a.id IN $s AND b.id IN $d RETURN b.id AS d",
                                           {"s": words, "d": dst}),
        "edge read, (b {id: $x}) map anchor + a.id IN": (
            "MATCH (a)-[r:CUES]->(b {id: $x}) WHERE a.id IN $s RETURN a.id AS s", {"s": words, "x": dst[0]}),
        "edge read, (a {id: $x}) map anchor + b.id IN": (
            "MATCH (a {id: $x})-[r:CUES]->(b) WHERE b.id IN $d RETURN b.id AS d", {"x": words[0], "d": dst}),
        "node read, (a:Api) WHERE a.id IN $list": ("MATCH (a:Api) WHERE a.id IN $d RETURN a.id AS id", {"d": dst}),
    }
    res["anchor_shapes_median_ms_of_7"] = {}
    for name, (q, p) in shapes.items():
        ts = []
        for _ in range(7):
            t = time.perf_counter()
            g.query(q, p)
            ts.append((time.perf_counter() - t) * 1000)
        res["anchor_shapes_median_ms_of_7"][name] = round(sorted(ts)[3], 2)
    try:
        g.query("UNWIND $rows AS row MATCH (a {id: row.s})-[r:CUES]->(b) RETURN b.id AS d", {"rows": [{"s": words[0]}]})
        res["unwind_read"] = "accepted"
    except Exception as e:  # recorded as evidence of the limit
        res["unwind_read"] = f"rejected: {e}".splitlines()[0][:300]
    if a.container:
        logs = subprocess.run(["docker", "logs", a.container], capture_output=True, text=True).stdout
        paths = Counter(m.group(1) for m in re.finditer(r'"hydradb.query.access_path":"([^"]+)"', logs))
        res["server_plan_warnings_access_path_counts_whole_container_life"] = dict(paths.most_common())
    OUT.write_text(json.dumps(res, indent=2) + "\n")
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
