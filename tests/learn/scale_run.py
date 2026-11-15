"""Fixture-scale run of the learner: how much record() and maintain() cost on a graph of realistic size.

    uv run python tests/learn/scale_run.py --url http://127.0.0.1:18443 [--apis 20000 --versions 20 --episodes 300]

Builds a SYNTHETIC library (not real PyPI data) in the contract schema: APIs over a version ladder with signatures,
removals with REPLACED_BY chains (some through ALIAS_OF), re-exports, Term -CUES-> Api. Then feeds synthetic
episodes (the code calls the replacement the context offered; outcomes drawn with a fixed seed, failures carry real
Python error messages) through GraphLearner.record, runs maintain() twice, builds PlainStore.from_graph and times its
context_for. Writes evidence/learn/scale-fixture.json and the move log evidence/learn/scale-fixture.jsonl.
These are cost/mechanics numbers on fixture data, NOT accuracy numbers (the curve is Phase 2 on GitChameleon).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import uuid
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bench.load.run import measure  # noqa: E402
from pinned.contract import (  # noqa: E402
    ADDED_IN, ALIAS_OF, API, CUES, HAS_VERSION, LIBRARY, NEXT, REMOVED_IN, REPLACED_BY, SHORTCUT, TERM, VERSION,
    Context, ContextItem, Episode, Hop,
)
from pinned.graph import (  # noqa: E402
    HydraGraph, count_edges, link_many, link_props_many, node_id, upsert_nodes, version_ord,
)
from pinned.learn import GraphLearner, PlainStore  # noqa: E402

OUT = ROOT / "evidence" / "learn"
RUN_ID = "scale-fixture"


def build(g: HydraGraph, lib: str, mod: str, n_apis: int, n_versions: int, seed: int) -> dict:
    rnd = random.Random(seed)
    versions = [f"1.{i}.0" for i in range(n_versions)]
    upsert_nodes(g, LIBRARY, [{"key": lib, "name": lib, "import_names": mod}])
    upsert_nodes(g, VERSION, [{"key": f"{lib}|{v}", "library": lib, "version": v, "ord": version_ord(v)} for v in versions])
    vid = {v: node_id(VERSION, f"{lib}|{v}") for v in versions}
    link_many(g, HAS_VERSION, [(node_id(LIBRARY, lib), vid[v]) for v in versions])
    link_many(g, NEXT, [(vid[a], vid[b]) for a, b in zip(versions, versions[1:])])
    paths = [f"{mod}.m{i % 60}.f{i}" for i in range(n_apis)]
    aid = {p: node_id(API, f"{lib}|{p}") for p in paths}
    upsert_nodes(g, API, [{"key": f"{lib}|{p}", "library": lib, "path": p, "kind": "function"} for p in paths])
    added, removed, replaced = [], [], []
    born: dict[str, int] = {}
    for p in paths:
        b = rnd.randrange(0, n_versions // 2)
        born[p] = b
        added.append((aid[p], vid[versions[b]], {"sig": f"(x, k{rnd.randrange(9)}=0)", "evidence": "synthetic"}))
    removals: list[tuple[str, str, int]] = []
    pool = paths[:]
    rnd.shuffle(pool)
    for old, new in zip(pool[: n_apis // 10], pool[n_apis // 10 : n_apis // 5]):
        r = rnd.randrange(born[old] + 1, n_versions)
        removed.append((aid[old], vid[versions[r]], {"evidence": "synthetic"}))
        if born[new] >= r:
            continue
        replaced.append((aid[old], aid[new], {"w": 1.0, "evidence": "synthetic", "since_ord": version_ord(versions[r])}))
        removals.append((old, new, r))
    # re-exports: mod.fK -> mod.mX.fK for 2% of APIs (same signature at every version)
    reexports = paths[n_apis // 5 : n_apis // 5 + n_apis // 50]
    rx_paths = [f"{mod}.{p.rsplit('.', 1)[-1]}" for p in reexports]
    upsert_nodes(g, API, [{"key": f"{lib}|{q}", "library": lib, "path": q, "kind": "function"} for q in rx_paths])
    for p, q in zip(reexports, rx_paths):
        sig = next(a[2]["sig"] for a in added if a[0] == aid[p])
        added.append((node_id(API, f"{lib}|{q}"), vid[versions[born[p]]], {"sig": sig, "evidence": "synthetic"}))
    link_props_many(g, ADDED_IN, added)
    link_props_many(g, REMOVED_IN, removed)
    link_props_many(g, REPLACED_BY, replaced)
    words = [f"w{i}x{seed}" for i in range(2000)]
    upsert_nodes(g, TERM, [{"key": w, "word": w} for w in words])
    cues = []
    cue_words: dict[str, list[str]] = {}
    for p in paths:
        ws = rnd.sample(words, 3)
        cue_words[p] = ws
        cues += [(node_id(TERM, w), aid[p], {"w": 1.0, "source": "doc"}) for w in ws]
    link_props_many(g, CUES, cues)
    return {"versions": versions, "removals": removals, "cue_words": cue_words, "apis": len(paths) + len(rx_paths),
            "added_in": len(added), "removed_in": len(removed), "replaced_by": len(replaced), "cues": len(cues)}


def episodes(lib: str, mod: str, world: dict, n: int, seed: int) -> list[Episode]:
    rnd = random.Random(seed + 1)
    out = []
    removals = world["removals"]
    for i in range(n):
        old, new, r = removals[i % len(removals)] if i < 2 * len(removals) else rnd.choice(removals)
        v = world["versions"][rnd.randrange(r, len(world["versions"]))]
        chain = [Hop(old, REMOVED_IN, f"{lib} {world['versions'][r]}"), Hop(old, REPLACED_BY, new)]
        ctx = Context(lib, v, [ContextItem(lib, new, "valid", chain, "(x)")])
        call = new[len(mod) + 1 :]
        roll = rnd.random()
        if roll < 0.7:
            passed, et, msg, code = True, "", "", f"import {mod}\n{mod}.{call}(1)\n"
        elif roll < 0.85:
            name = new.rsplit(".", 1)[-1]
            passed, et = False, "TypeError"
            msg = f"TypeError: {name}() got an unexpected keyword argument 'k99'"
            code = f"import {mod}\n{mod}.{call}(1, k99=2)\n"
        else:
            passed, et, msg = False, "AttributeError", f"module '{mod}.m1' has no attribute 'nope{i}'"
            code = f"import {mod}\n{mod}.{call}(1)\n{mod}.m1.nope{i}()\n"
        problem = " ".join(world["cue_words"][old][:2] + ["compute", "the", "result"])
        out.append(Episode(RUN_ID, f"sp{i}", 1, lib, v, problem, "", ctx, code, passed, et, msg))
    return out


class CountingIngestor:
    def __init__(self):
        self.calls = 0

    def ingest_version(self, library, version):
        raise NotImplementedError("not used in the scale run")

    def fill_gap(self, library, version, hint):
        from pinned.contract import IngestReport

        self.calls += 1
        return IngestReport(library, version)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--apis", type=int, default=20000)
    ap.add_argument("--versions", type=int, default=20)
    ap.add_argument("--episodes", type=int, default=300)
    ap.add_argument("--seed", type=int, default=13)
    a = ap.parse_args()
    g = HydraGraph(url=a.url)
    k = uuid.uuid4().hex[:8]
    lib, mod = f"scale{k}", f"s{k}"
    t = time.perf_counter()
    world = build(g, lib, mod, a.apis, a.versions, a.seed)
    build_s = time.perf_counter() - t
    log_path = OUT / f"{RUN_ID}.jsonl"
    if log_path.exists():
        log_path.unlink()
    ing = CountingIngestor()
    learner = GraphLearner(g, ingestor=ing, run_id=RUN_ID, log_dir=OUT)
    eps = episodes(lib, mod, world, a.episodes, a.seed)
    reqs0 = g.requests
    reports = []
    rec = measure(lambda ep: reports.append(learner.record(ep)), eps, 1)
    rec_requests = (g.requests - reqs0) / len(eps)
    m_times, m_reports = [], []

    def run_maintain() -> None:
        t0 = time.perf_counter()
        m_reports.append(learner.maintain())
        m_times.append(round(time.perf_counter() - t0, 3))

    run_maintain()  # first pass in this process: full merge + decay scans
    second = [replace(e, problem_id=e.problem_id + "b") for e in episodes(lib, mod, world, a.episodes, a.seed + 7)]
    rec8 = measure(learner.record, second[: a.episodes // 2], 8)
    run_maintain()  # incremental: only touched edges / dirty libraries
    run_maintain()  # nothing new
    t = time.perf_counter()
    store = PlainStore.from_graph(g, lib)
    export_s = time.perf_counter() - t
    probs = [(e.problem, e.version) for e in eps[:200]]
    plain_ctx = measure(lambda pv: store.context_for(pv[0], "", lib, pv[1]), probs, 1)
    moves = Counter(m["move"] for m in learner.log.read())
    sc_rows = g.query(f"MATCH (t:{TERM})-[r:{SHORTCUT}]->(a) WHERE a.library = $lib RETURN r.hops AS hops", {"lib": lib})
    result = {
        "what": "SYNTHETIC fixture-scale cost run of pinned.learn (not real data, not accuracy)",
        "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hydradb_url": a.url, "seed": a.seed,
        "graph": {k_: v for k_, v in world.items() if isinstance(v, int)} | {"versions": len(world["versions"])},
        "build_seconds": round(build_s, 1),
        "episodes": len(eps) + len(eps) // 2,
        "record_c1": rec, "record_c8": rec8, "record_graph_requests_per_call": round(rec_requests, 2),
        "record_totals": {
            "edges_reweighted": sum(r.edges_reweighted for r in reports),
            "gaps_ingested": sum(r.gaps_ingested for r in reports), "fill_gap_calls": ing.calls,
        },
        "maintain_seconds": {"first_full_pass": m_times[0], "after_150_more_episodes": m_times[1],
                             "nothing_new": m_times[2]},
        "maintain_reports": [{"shortcuts_added": r.shortcuts_added, "merged": r.merged, "split": r.split,
                              "edges_reweighted": r.edges_reweighted, "notes": r.notes} for r in m_reports],
        "shortcut_edges_in_graph": len(sc_rows),
        "learned_cue_edges_in_graph": g.query(
            f"MATCH (t:{TERM})-[r:{CUES}]->(a) WHERE a.library = $lib AND r.source = 'learned' RETURN count(*) AS n",
            {"lib": lib})[0]["n"],
        "moves_logged": dict(sorted(moves.items())),
        "plainstore": {"export_seconds": round(export_s, 2), "paths": len(store.libs[lib].events),
                       "words": len(store.libs[lib].cues), "context_for_c1": plain_ctx},
        "alias_edges_total_on_node": count_edges(g, API, ALIAS_OF),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{RUN_ID}.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
