"""Latency of GraphSearch at realistic scale (scope item 7). Not a pytest test: run it by hand.

    uv run python -m tests.search.bench_latency --url http://127.0.0.1:18443

Builds a synthetic graph under a fresh prefix in exactly the contract.py schema: 5 libraries x 24 versions x 4,400 Api
(22,000 Api) with ADDED_IN / REMOVED_IN (some re-added) / CHANGED_IN (signature + deprecated) facts, REPLACED_BY and
ALIAS_OF chains (1-3 hops), a Zipf-like word vocabulary with CUES edges and SHORTCUT edges. Then measures
`context_for` and `check_calls` at concurrency 1 / 8 / 32 with bench.load.run.measure and writes
evidence/search/latency.json (p50/p95/p99, qps, errors, graph round trips per call). The `latency` block is warm
(the process has already seen the vocabulary once, see facts.TermStats); the cold first pass is reported apart.
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
from datetime import datetime, timezone
from pathlib import Path

from bench.load.run import measure
from pinned.graph import HydraGraph, count_edges, count_nodes
from pinned.search import GraphSearch
from tests.search.search_fixture_graph import LibBuilder

OUT = Path(__file__).resolve().parents[2] / "evidence" / "search"


def build_library(g: HydraGraph, name: str, imp: str, n_versions: int, n_apis: int, n_words: int,
                  rnd: random.Random) -> dict:
    versions = [f"1.{i}.0" for i in range(n_versions)]
    b = LibBuilder(g, name, imp, versions)
    vocab = [f"w{name[-4:]}{i}" for i in range(n_words)]
    zipf = [1.0 / (i + 1) for i in range(n_words)]
    modules = [f"mod{i}" for i in range(40)]
    paths, removed, added_later_pairs = [], [], []
    info: dict[str, dict] = {}
    for i in range(n_apis):
        mod = modules[i % len(modules)]
        path = f"{mod}.Cls{i % 97}.f{i}" if i % 3 == 0 else f"{mod}.f{i}"
        a = rnd.randrange(n_versions - 1)
        params = [f"p{j}" for j in range(rnd.randrange(1, 5))]
        sig = "(" + ", ".join(params + ["opt=None"]) + ")"
        added = [(versions[a], sig)]
        rem: list[str] = []
        changed: list[tuple[str, str, str, str]] = []
        r = rnd.random()
        if r < 0.15 and a + 2 < n_versions:
            gone = rnd.randrange(a + 1, n_versions)
            rem.append(versions[gone])
            if rnd.random() < 0.2 and gone + 1 < n_versions:
                added.append((versions[rnd.randrange(gone + 1, n_versions)], sig))
            removed.append(path)
        elif r < 0.25 and a + 1 < n_versions:
            new_sig = sig.replace("opt=None", "option=None")
            changed.append((versions[rnd.randrange(a + 1, n_versions)], "signature", sig, new_sig))
        elif r < 0.30 and a + 1 < n_versions:
            changed.append((versions[rnd.randrange(a + 1, n_versions)], "deprecated", "", f"use {imp}.{mod}.g{i}"))
        b.api(path, kind="method" if "Cls" in path else "function", added=added, removed=rem, changed=changed)
        info[path] = {"added": a, "params": params}
        paths.append(path)
        for w in rnd.choices(vocab, weights=zipf, k=rnd.randrange(2, 6)):
            b.cue(w, path, w=round(rnd.uniform(0.3, 1.5), 3), source=rnd.choice(["name", "doc"]))
    for path in removed:   # chains: 60% one hop, 25% REPLACED_BY + ALIAS_OF, 15% three hops
        t1, t2, t3 = rnd.sample(paths, 3)
        roll = rnd.random()
        b.replace(path, t1, w=1.0)
        if roll >= 0.6:
            b.alias(t1, t2)
        if roll >= 0.85:
            b.replace(t2, t3, w=0.9)
    for path in rnd.sample(paths, n_apis // 50):   # newer APIs that replaced an older one (walk back)
        old = rnd.choice(paths)
        b.replace(old, path, w=1.0)
        added_later_pairs.append((old, path))
    for w in rnd.sample(vocab, 200):
        lo = rnd.randrange(n_versions - 1)
        b.shortcut(w, rnd.choice(paths), lo=versions[lo], hi=versions[rnd.randrange(lo, n_versions)], w=1.2)
    b.commit()
    return {"builder": b, "versions": versions, "paths": paths, "removed": removed, "vocab": vocab, "zipf": zipf,
            "info": info}


def make_calls(libs: list[dict], n: int, rnd: random.Random) -> tuple[list[tuple], list[tuple]]:
    ctx_calls, check_calls = [], []
    for _ in range(n):
        lib = rnd.choice(libs)
        b: LibBuilder = lib["builder"]
        version = rnd.choice(lib["versions"][4:])
        words = rnd.choices(lib["vocab"], weights=lib["zipf"], k=rnd.randrange(6, 14))
        problem = "Write a function that " + " ".join(words) + " and returns the result."
        used = rnd.sample(lib["paths"], 3) + rnd.sample(lib["removed"], 1)
        lines = [f"import {b.imp} as lib"]
        for p in used:
            mod, _, rest = p.partition(".")
            lines.append(f"from {b.imp}.{mod} import {rest.split('.')[0]}")
        lines.append("def solve(x):")
        for p in used:
            kw = ", opt=1" if rnd.random() < 0.5 else ", option=1"
            lines.append(f"    y = lib.{p}(x{kw})")
        lines.append("    return y")
        code = "\n".join(lines) + "\n"
        ctx_calls.append((problem, "\n".join(lines[: 1 + len(used)]) + "\n", b.name, version))
        check_calls.append((code, b.name, version))
    return ctx_calls, check_calls


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("HYDRA_URL", "http://127.0.0.1:8443"))
    ap.add_argument("--libs", type=int, default=5)
    ap.add_argument("--versions", type=int, default=24)
    ap.add_argument("--apis", type=int, default=22000)
    ap.add_argument("--words", type=int, default=1500)
    ap.add_argument("--calls", type=int, default=400)
    ap.add_argument("--seed", type=int, default=13)
    a = ap.parse_args()
    rnd = random.Random(a.seed)
    g = HydraGraph(url=a.url, pool=64)
    run = "sb" + uuid.uuid4().hex[:6]
    t0 = time.perf_counter()
    libs = [build_library(g, f"{run}lib{i}", f"{run}m{i}", a.versions, a.apis // a.libs, a.words, rnd)
            for i in range(a.libs)]
    build_s = time.perf_counter() - t0
    print(f"built {a.apis} Api in {build_s:.1f}s")

    search = GraphSearch(g, workers=8)
    ctx_calls, chk_calls = make_calls(libs, a.calls, rnd)

    # Pass 1 (cold: a fresh GraphSearch has never seen these words, so it expands every word once and learns which are
    # hubs). Sequential, so the shared client counter is exact (and must equal ctx.graph_queries).
    ctx_q, chk_q, items, mismatch, cold_ms = [], [], [], 0, []
    for args in ctx_calls[:100]:
        before = g.requests
        t = time.perf_counter()
        ctx = search.context_for(*args, k=8)
        cold_ms.append((time.perf_counter() - t) * 1000)
        ctx_q.append(g.requests - before)
        mismatch += ctx.graph_queries != ctx_q[-1]
        items.append(len(ctx.items))
    for args in chk_calls[:100]:
        before = g.requests
        search.check_calls(*args)
        chk_q.append(g.requests - before)

    def q_stats(xs: list[int]) -> dict:
        return {"mean": round(statistics.fmean(xs), 2), "min": min(xs), "max": max(xs),
                "p50": sorted(xs)[len(xs) // 2]}

    def ctx_fn(args):
        return search.context_for(*args, k=8)

    def chk_fn(args):
        return search.check_calls(*args)

    # Pass 2 (warm): every call below runs on the same instance after pass 1.
    for args in ctx_calls:
        ctx_fn(args)
    warm_q = []
    for args in ctx_calls[:100]:
        before = g.requests
        ctx_fn(args)
        warm_q.append(g.requests - before)
    results = {
        "context_for": [measure(ctx_fn, ctx_calls, c) for c in (1, 8, 32)],
        "check_calls": [measure(chk_fn, chk_calls, c) for c in (1, 8, 32)],
    }
    digest = subprocess.run(["docker", "image", "inspect", "ghcr.io/hydra-db/hydradb:0.4.0", "--format",
                             "{{index .RepoDigests 0}}"], capture_output=True, text=True).stdout.strip()
    prefix_api = sum(len(lib["paths"]) for lib in libs)
    out = {
        "kind": "synthetic-search", "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hydradb_image": digest or "unknown",
        "machine": {"cpu_count": os.cpu_count(), "platform": platform.platform(), "note": "client and HydraDB node on the same machine"},
        "params": vars(a), "run_prefix": run, "build_seconds": round(build_s, 1),
        "graph": {
            "this_run": {"libraries": a.libs, "versions_per_library": a.versions, "api": prefix_api,
                         "removed_api": sum(len(lib["removed"]) for lib in libs), "words": a.words * a.libs},
            "whole_node": {"Api": count_nodes(g, "Api"), "Term": count_nodes(g, "Term"),
                           "CUES": count_edges(g, "Term", "CUES"), "ADDED_IN": count_edges(g, "Api", "ADDED_IN"),
                           "REMOVED_IN": count_edges(g, "Api", "REMOVED_IN"),
                           "CHANGED_IN": count_edges(g, "Api", "CHANGED_IN"),
                           "REPLACED_BY": count_edges(g, "Api", "REPLACED_BY"),
                           "ALIAS_OF": count_edges(g, "Api", "ALIAS_OF"),
                           "SHORTCUT": count_edges(g, "Term", "SHORTCUT")},
        },
        "context_for_cold_first_100_sequential_ms": {
            "p50": round(sorted(cold_ms)[50], 2), "p95": round(sorted(cold_ms)[95], 2), "max": round(max(cold_ms), 2),
            "note": "first time this process sees the words: every word is expanded once (hub cost paid here)"},
        "graph_queries_per_call": {"context_for_cold": q_stats(ctx_q), "context_for_warm": q_stats(warm_q),
                                   "check_calls": q_stats(chk_q),
                                   "context_for_counter_mismatches": mismatch,
                                   "context_for_items_mean": round(statistics.fmean(items), 2)},
        "latency": results,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "latency.json"
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: out[k] for k in ("graph", "context_for_cold_first_100_sequential_ms", "graph_queries_per_call",
                                          "latency")}, indent=1))
    print("saved", path)
    search.close()
    g.close()


if __name__ == "__main__":
    main()
