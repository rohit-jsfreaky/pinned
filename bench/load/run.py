"""Load bench for HydraDB with Pinned-shaped data and Pinned-shaped queries.

    uv run python -m bench.load.run --url http://127.0.0.1:18443 --apis 20000

Builds a synthetic version graph under a fresh run prefix (libraries -> versions -> APIs with ADDED_IN / REMOVED_IN,
REPLACED_BY chains, Term -CUES-> Api), measures write throughput per write shape, then read latency for the four query
shapes search uses, at concurrency 1 / 8 / 32. Writes evidence/load/<utc>-synthetic.json.
`measure(fn, calls, concurrency)` is reused later to bench the real `context_for` on the real graph.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import statistics
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pinned.contract import ADDED_IN, API, CUES, LIBRARY, REMOVED_IN, REPLACED_BY, TERM, VERSION
from pinned.graph import HydraGraph, edges_from, link, link_many, link_props_many, multi_paths, reach, upsert_nodes, version_ord

OUT = Path(__file__).resolve().parents[2] / "evidence" / "load"


def _pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def measure(fn: Callable[[Any], Any], calls: list[Any], concurrency: int) -> dict:
    """Run fn(arg) for every arg with `concurrency` threads; latency stats in ms, throughput in calls/s."""
    lat: list[float] = []
    errors = 0

    def one(arg):
        nonlocal errors
        t = time.perf_counter()
        try:
            fn(arg)
        except Exception:
            errors += 1
        lat.append((time.perf_counter() - t) * 1000)

    t0 = time.perf_counter()
    with ThreadPoolExecutor(concurrency) as pool:
        list(pool.map(one, calls))
    wall = time.perf_counter() - t0
    return {
        "concurrency": concurrency, "calls": len(calls), "errors": errors, "qps": round(len(calls) / wall, 1),
        "p50_ms": round(_pct(lat, 50), 2), "p95_ms": round(_pct(lat, 95), 2), "p99_ms": round(_pct(lat, 99), 2),
        "mean_ms": round(statistics.fmean(lat), 2),
    }


def _timed(fn: Callable[[], Any]) -> float:
    t = time.perf_counter()
    fn()
    return time.perf_counter() - t


def build(g: HydraGraph, run: str, libs: int, versions: int, apis: int, terms: int, seed: int) -> dict:
    rnd = random.Random(seed)
    per_lib = apis // libs
    stats: dict[str, Any] = {}
    lib_rows = [{"key": f"{run}lib{i}", "name": f"{run}lib{i}"} for i in range(libs)]
    ver_rows, api_rows, added, removed, replaced = [], [], [], [], []
    for li in range(libs):
        lib = f"{run}lib{li}"
        vers = [f"1.{m}.0" for m in range(versions)]
        ver_rows += [{"key": f"{lib}|{v}", "library": lib, "version": v, "ord": version_ord(v)} for v in vers]
        for ai in range(per_lib):
            path = f"{lib}.mod{ai % 50}.f{ai}"
            api_rows.append({"key": f"{lib}|{path}", "library": lib, "path": path, "kind": "function"})
    t = _timed(lambda: upsert_nodes(g, LIBRARY, lib_rows))
    t += _timed(lambda: upsert_nodes(g, VERSION, ver_rows))
    api_ids = []
    t += _timed(lambda: api_ids.extend(upsert_nodes(g, API, api_rows)))
    term_ids = upsert_nodes(g, TERM, [{"key": f"{run}w{i}", "word": f"{run}w{i}"} for i in range(terms)])
    n_nodes = len(lib_rows) + len(ver_rows) + len(api_rows) + terms
    stats["node_batch_rows_per_s"] = round(n_nodes / t, 1)
    ver_ids = {r["key"]: i for r, i in zip(ver_rows, upsert_nodes(g, VERSION, ver_rows))}
    for row, aid in zip(api_rows, api_ids):
        lib = row["library"]
        a = rnd.randrange(versions)
        added.append((aid, ver_ids[f"{lib}|1.{a}.0"]))
        if rnd.random() < 0.15 and a + 1 < versions:
            removed.append((aid, ver_ids[f"{lib}|1.{rnd.randrange(a + 1, versions)}.0"]))
    for aid, _ in removed:
        replaced.append((aid, rnd.choice(api_ids), {"w": 1.0, "evidence": "synthetic"}))
    cues = [(rnd.choice(term_ids), rnd.choice(api_ids), {"w": rnd.random(), "source": "doc"}) for _ in range(apis)]
    t = _timed(lambda: (link_many(g, ADDED_IN, added), link_many(g, REMOVED_IN, removed)))
    stats["edge_batch_rows_per_s"] = round((len(added) + len(removed)) / t, 1)
    stats["edge_props_batch_rows_per_s"] = round(len(cues) / _timed(lambda: link_props_many(g, CUES, cues)), 1)
    one = cues[:200]  # the slow shape, for the record: one MERGE per edge
    stats["edge_props_single_merge_rows_per_s"] = round(len(one) / _timed(lambda: [link(g, s, CUES, d, p) for s, d, p in one]), 1)
    link_props_many(g, REPLACED_BY, replaced)
    stats.update(nodes=n_nodes, edges=len(added) + len(removed) + len(cues) + len(replaced))
    return {"stats": stats, "api_ids": api_ids, "removed": [a for a, _ in removed], "term_ids": term_ids,
            "api_keys": [r["key"] for r in api_rows]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("HYDRA_URL", "http://127.0.0.1:8443"))
    ap.add_argument("--libs", type=int, default=10)
    ap.add_argument("--versions", type=int, default=20)
    ap.add_argument("--apis", type=int, default=20000)
    ap.add_argument("--terms", type=int, default=2000)
    ap.add_argument("--calls", type=int, default=400)
    ap.add_argument("--seed", type=int, default=13)
    a = ap.parse_args()
    g = HydraGraph(url=a.url, pool=64)
    run = "bench" + uuid.uuid4().hex[:6] + "_"
    t0 = time.perf_counter()
    data = build(g, run, a.libs, a.versions, a.apis, a.terms, a.seed)
    build_s = time.perf_counter() - t0
    rnd = random.Random(a.seed)
    shapes: dict[str, tuple[Callable[[Any], Any], list[Any]]] = {
        # status of 8 candidate APIs at once: ADDED_IN + REMOVED_IN with version ord
        "validity_8_apis": (lambda ids: (edges_from(g, ids, ADDED_IN, dst_props=("ord",)),
                                         edges_from(g, ids, REMOVED_IN, dst_props=("ord",))),
                            [rnd.sample(data["api_ids"], 8) for _ in range(a.calls)]),
        # replacement chain from a removed API
        "replacement_chain_sspaths": (lambda s: reach(g, s, [REPLACED_BY, ADDED_IN], max_len=3, limit=50),
                                      [rnd.choice(data["removed"]) for _ in range(a.calls)]),
        # top CUES for one term
        "cues_top20": (lambda t: g.query(f"MATCH (t:{TERM} {{id: $t}})-[r:{CUES}]->(x) RETURN x.key AS k, r.w AS w "
                                         "ORDER BY w DESC, k ASC LIMIT 20", {"t": t}),
                       [rnd.choice(data["term_ids"]) for _ in range(a.calls)]),
        # many seeds in one request
        "mspaths_8x8": (lambda pair: multi_paths(g, API, pair[0], pair[1], [REPLACED_BY], max_len=2),
                        [(rnd.sample(data["api_keys"], 8), rnd.sample(data["api_keys"], 8)) for _ in range(a.calls)]),
    }
    reads = {name: [measure(fn, calls, c) for c in (1, 8, 32)] for name, (fn, calls) in shapes.items()}
    digest = subprocess.run(["docker", "image", "inspect", "ghcr.io/hydra-db/hydradb:0.4.0", "--format",
                             "{{index .RepoDigests 0}}"], capture_output=True, text=True).stdout.strip()
    result = {
        "kind": "synthetic", "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "url": a.url,
        "hydradb_image": digest or "unknown", "machine": {"cpu_count": os.cpu_count(), "platform": platform.platform()},
        "params": vars(a), "build_seconds": round(build_s, 1), "writes": data["stats"], "reads": reads,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{result['utc'].replace(':', '')}-synthetic.json"
    path.write_text(json.dumps(result, indent=2))
    print(json.dumps({"writes": result["writes"], "reads": reads}, indent=1))
    print("saved", path)


if __name__ == "__main__":
    main()
