"""Real-graph latency: `context_for` and `check_calls` on the loaded full graph (26 libraries, ~1M Api nodes).

    HYDRA_URL=http://127.0.0.1:18443 uv run python -m bench.load.real --n 100

Inputs are the first N TEST problems of evidence/split.json: (problem text, starting_code, library, version) — the
same inputs an agent sends. check_calls gets the problem's STARTING code (never a solution). Pass 1 is cold and
sequential (exact graph round trips per call from the client counter); then concurrency 1 / 8 / 32 warm with
bench.load.run.measure. Writes evidence/search/latency-real.json.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from pinned.eval import dataset as ds
from pinned.eval.split import SPLIT_PATH, load_split
from pinned.graph import HydraGraph
from pinned.search import GraphSearch

from .run import _pct, measure

OUT = Path(__file__).resolve().parents[2] / "evidence" / "search" / "latency-real.json"


def _stats(xs: list[float]) -> dict:
    return {"mean": round(statistics.fmean(xs), 2), "min": min(xs), "max": max(xs), "p50": _pct(xs, 50),
            "p95": _pct(xs, 95)}


def _image() -> str:
    try:
        return subprocess.run(["docker", "inspect", "-f", "{{.Image}}", "pinned-g"], capture_output=True, text=True, check=False,
                              timeout=10).stdout.strip()
    except OSError:
        return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("HYDRA_URL", "http://127.0.0.1:18443"))
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--k", type=int, default=8)
    a = ap.parse_args()
    probs = ds.by_id(ds.load_problems(ds.DEFAULT_DATA_DIR))
    test_ids = load_split(SPLIT_PATH).test[: a.n]
    chosen = [probs[i] for i in test_ids]
    ctx_calls = [(p.problem, p.starting_code, p.library, p.version) for p in chosen]
    chk_calls = [(p.starting_code, p.library, p.version) for p in chosen]

    g = HydraGraph(url=a.url, pool=64)
    search = GraphSearch(g)
    cold_ms, ctx_q, items, mismatch, chk_q, chk_ms = [], [], [], 0, [], []
    for args in ctx_calls:                      # pass 1: cold, sequential, exact round-trip counts
        before = g.requests
        t = time.perf_counter()
        ctx = search.context_for(*args, k=a.k)
        cold_ms.append((time.perf_counter() - t) * 1000)
        ctx_q.append(g.requests - before)
        mismatch += ctx.graph_queries != ctx_q[-1]
        items.append(len(ctx.items))
    for args in chk_calls:
        before = g.requests
        t = time.perf_counter()
        search.check_calls(*args)
        chk_ms.append((time.perf_counter() - t) * 1000)
        chk_q.append(g.requests - before)

    out = {
        "kind": "real-graph-search",
        "utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "hydradb_image": _image(),
        "machine": {"cpu_count": os.cpu_count(), "platform": platform.platform(),
                    "note": "client and HydraDB node on the same machine (4 vCPU cloud VM); eval jobs ran in parallel"},
        "graph": "full Pinned graph from branch graph-snapshot (sha256 aced2bde...84f5): 26 libraries, 282 versions, "
                 "~1.01M Api nodes, ~4.64M edges",
        "inputs": {"source": "evidence/split.json TEST, first N ids", "n": len(chosen), "k": a.k,
                   "libraries": sorted({p.library for p in chosen}),
                   "check_calls_code": "the problem's starting_code (no solutions)"},
        "context_for_cold_first_pass_sequential_ms": {"p50": round(_pct(cold_ms, 50), 2),
                                                       "p95": round(_pct(cold_ms, 95), 2),
                                                       "max": round(max(cold_ms), 2)},
        "check_calls_cold_first_pass_sequential_ms": {"p50": round(_pct(chk_ms, 50), 2),
                                                       "p95": round(_pct(chk_ms, 95), 2),
                                                       "max": round(max(chk_ms), 2)},
        "graph_queries_per_call": {"context_for_cold": _stats(ctx_q), "check_calls_cold": _stats(chk_q),
                                   "context_for_counter_mismatches": mismatch},
        "context_for_items": _stats(items),
        "latency": {
            "context_for": [measure(lambda x: search.context_for(*x, k=a.k), ctx_calls, c) for c in (1, 8, 32)],
            "check_calls": [measure(lambda x: search.check_calls(*x), chk_calls, c) for c in (1, 8, 32)],
        },
        "latency_note": "warm: same GraphSearch instance after the cold pass",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({k: out[k] for k in ("context_for_cold_first_pass_sequential_ms", "graph_queries_per_call",
                                          "latency")}, indent=1))


if __name__ == "__main__":
    main()
