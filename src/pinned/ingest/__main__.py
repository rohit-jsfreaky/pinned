"""Ingestion CLI.

  uv run python -m pinned.ingest --all                       # dataset pairs -> ladders -> every version -> summary
  uv run python -m pinned.ingest --lib scipy --version 1.7.3 # one version (and the not-yet-ingested rungs below it)
  uv run python -m pinned.ingest --ladder scipy              # every rung of one library's ladder
  uv run python -m pinned.ingest --gap scipy 1.7.3 scipy.special.i1
  uv run python -m pinned.ingest --summary                   # recompute evidence/ingest/summary.json from the graph

The graph is the node at $HYDRA_URL (default http://127.0.0.1:8443). Evidence goes to evidence/ingest/.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

from pinned.graph import HydraGraph

from .dataset import download, load_pairs, save_pairs
from .ingestor import DEFAULT_CACHE, PyPIIngestor, ROOT
from .ladder import save_ladders
from .summary import coverage, library_counts, write_json

EVIDENCE = ROOT / "evidence" / "ingest"
DATA = ROOT / "data"
# Ingest order for --all: small libraries first, the biggest (torch, librosa) last.
LAST = ("librosa", "torch")


def _prebuild(lib: str, cache_dir: str, dynamic: bool) -> tuple[str, float, list[str]]:
    """Worker process: warm the snapshot cache of every rung of one library (download, static load, dynamic)."""
    logging.basicConfig(level=logging.WARNING)
    t0 = time.time()
    ing = PyPIIngestor(graph=None, cache_dir=cache_dir, dynamic=dynamic)  # type: ignore[arg-type]  # no graph use
    errors = []
    for rung in ing.ladder(lib).rungs:
        try:
            ing.snapshot(lib, rung)
        except Exception as e:  # report and continue with the other rungs
            errors.append(f"{rung.version}: {type(e).__name__}: {e}"[:500])
    return lib, time.time() - t0, errors


def prepare(dataset_refresh: bool = False) -> tuple[list, str]:
    path = DATA / "gitchameleon" / "dataset.jsonl"
    sha_file = DATA / "gitchameleon" / "commit.txt"
    if dataset_refresh or not path.exists() or not sha_file.exists():
        path, sha = download(DATA)
        sha_file.write_text(sha + "\n")
    sha = sha_file.read_text().strip()
    pairs = load_pairs(path)
    save_pairs(pairs, EVIDENCE / "pairs.json", sha)
    print(f"pairs: {len(pairs)} (library, version) pairs, {len({p.library for p in pairs})} libraries, dataset {sha[:12]}")
    return pairs, sha


def order(libs: list[str], sizes: dict[str, int]) -> list[str]:
    head = sorted((l for l in libs if l not in LAST), key=lambda l: (sizes.get(l, 0), l))
    return head + [l for l in LAST if l in libs]


def run_all(args: argparse.Namespace) -> None:
    pairs, _ = prepare(args.refresh_dataset)
    g = HydraGraph()
    ing = PyPIIngestor(g, cache_dir=args.cache, pairs=pairs, dynamic=not args.no_dynamic, report_dir=EVIDENCE / "reports")
    libs = sorted({p.library for p in pairs})
    ladders = [ing.ladder(l) for l in libs]
    save_ladders(ladders, EVIDENCE / "ladders.json")
    print(f"ladders: {sum(len(l.rungs) for l in ladders)} versions")
    sizes = {l.library: len(l.rungs) for l in ladders}
    libs = order(libs, sizes)
    if args.only:
        libs = [l for l in libs if l in args.only]
    load_seconds: dict[str, float] = {}
    if args.jobs > 1:
        print(f"prebuilding snapshots with {args.jobs} processes ...", flush=True)
        with ProcessPoolExecutor(args.jobs) as pool:
            futs = [pool.submit(_prebuild, l, str(args.cache), not args.no_dynamic) for l in libs]
            for f in as_completed(futs):
                lib, secs, errors = f.result()
                load_seconds[lib] = secs
                print(f"  snapshots {lib}: {secs:.0f}s" + (f" errors: {errors}" if errors else ""), flush=True)
    timing = {}
    for lib in libs:
        t0 = time.time()
        reports = ing.ingest_library(lib)
        timing[lib] = {"seconds": round(time.time() - t0, 2), "load_seconds_prebuild": round(load_seconds.get(lib, 0), 2)}
        print(f"{lib}: {len(reports)} versions, {sum(r.added for r in reports)} ADDED_IN, "
              f"{sum(r.removed for r in reports)} REMOVED_IN, {sum(r.replaced for r in reports)} REPLACED_BY "
              f"({timing[lib]['seconds']:.0f}s)", flush=True)
        write_json(EVIDENCE / "timing" / f"{lib}.json", timing[lib])
    summarize(g, args)


def summarize(g: HydraGraph, args: argparse.Namespace) -> None:
    ladders = json.loads((EVIDENCE / "ladders.json").read_text())["ladders"]
    ing = PyPIIngestor(g, cache_dir=args.cache, report_dir=EVIDENCE / "reports")
    out: dict = {"libraries": {}, "total": {"versions": 0, "api_nodes": 0, "edges": {}, "seconds": 0.0,
                                            "rows_written": 0}}
    import_names: dict[str, list[str]] = {}
    for lad in ladders:
        lib = lad["library"]
        counts = library_counts(g, lib, ing.known_api_ids(lib))
        reports = sorted((EVIDENCE / "reports" / lib).glob("*.json")) if (EVIDENCE / "reports" / lib).exists() else []
        rows_written, write_s, load_s = 0, 0.0, 0.0
        for f in reports:
            d = json.loads(f.read_text())
            if d.get("kind") != "version":
                continue
            rows_written += sum(d.get("edges_written", {}).values()) + d.get("nodes_written", 0)
            write_s += d.get("write_seconds", 0.0)
            load_s += d.get("load_seconds", 0.0)
        timing_f = EVIDENCE / "timing" / f"{lib}.json"
        timing = json.loads(timing_f.read_text()) if timing_f.exists() else {}
        seconds = round(timing.get("seconds", write_s + load_s) + timing.get("load_seconds_prebuild", 0.0), 2)
        counts.update({"ladder_versions": len(lad["rungs"]), "seconds": seconds,
                       "load_seconds": round(load_s + timing.get("load_seconds_prebuild", 0.0), 2),
                       "write_seconds": round(write_s, 2), "rows_written": rows_written,
                       "rows_per_s_write": round(rows_written / write_s, 1) if write_s else None})
        out["libraries"][lib] = counts
        t = out["total"]
        t["versions"] += counts["versions"]
        t["api_nodes"] += counts["api_nodes"]
        t["seconds"] += seconds
        t["rows_written"] += rows_written
        t["write_seconds"] = t.get("write_seconds", 0.0) + write_s
        for k, v in counts["edges"].items():
            t["edges"][k] = t["edges"].get(k, 0) + v
        lib_node = g.query("MATCH (l:Library) WHERE l.name = $n RETURN l.import_names AS i", {"n": lib})
        import_names[lib] = [x for x in (lib_node[0]["i"] if lib_node else "").split(",") if x]
    t = out["total"]
    t["seconds"] = round(t["seconds"], 2)
    t["write_seconds"] = round(t.get("write_seconds", 0.0), 2)
    t["rows_per_s_write"] = round(t["rows_written"] / t["write_seconds"], 1) if t.get("write_seconds") else None
    t["term_nodes"] = g.query("MATCH (t:Term) RETURN count(*) AS n")[0]["n"]
    dataset = DATA / "gitchameleon" / "dataset.jsonl"
    if dataset.exists():
        cov = coverage(g, dataset, import_names)
        write_json(EVIDENCE / "coverage.json", cov)
        out["coverage"] = {"total": cov["total"],
                           "per_library": {k: v["share_valid"] for k, v in cov["libraries"].items()},
                           "details": "evidence/ingest/coverage.json"}
    out["hydra_url"] = g.url
    write_json(EVIDENCE / "summary.json", out)
    print(json.dumps(out["total"], indent=1))
    if "coverage" in out:
        print("coverage:", json.dumps(out["coverage"]["total"]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pinned.ingest", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--all", action="store_true", help="every dataset pair + ladders, then summary + coverage")
    mode.add_argument("--lib", help="library of --version")
    mode.add_argument("--ladder", metavar="LIB", help="ingest every rung of LIB's ladder")
    mode.add_argument("--gap", nargs=3, metavar=("LIB", "VERSION", "HINT"), help="targeted deeper ingest")
    mode.add_argument("--summary", action="store_true", help="recompute summary.json + coverage.json from the graph")
    mode.add_argument("--pairs", action="store_true", help="only download the dataset and write pairs + ladders")
    ap.add_argument("--version", help="version for --lib")
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help="download / snapshot cache (default data/pypi-cache)")
    ap.add_argument("--jobs", type=int, default=1, help="--all: processes that prebuild snapshots in parallel")
    ap.add_argument("--only", nargs="*", help="--all: restrict to these libraries")
    ap.add_argument("--no-dynamic", action="store_true", help="static loading only (no venvs)")
    ap.add_argument("--refresh-dataset", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(asctime)s %(message)s")
    if not args.verbose:
        logging.getLogger("pinned.ingest.ingestor").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.pairs:
        pairs, _ = prepare(args.refresh_dataset)
        ing = PyPIIngestor(None, cache_dir=args.cache, pairs=pairs)  # type: ignore[arg-type]  # ladders need no graph
        ladders = [ing.ladder(l) for l in sorted({p.library for p in pairs})]
        save_ladders(ladders, EVIDENCE / "ladders.json")
        print(f"ladders: {sum(len(l.rungs) for l in ladders)} versions in {len(ladders)} libraries")
        return 0
    if args.all:
        run_all(args)
        return 0
    g = HydraGraph()
    if not g.ready():
        print(f"HydraDB at {g.url} is not answering /healthz (start one: infra/hydradb-up.sh <name> <http> <bolt> "
              "<admin>; set HYDRA_URL)", file=sys.stderr)
        return 2
    if args.summary:
        summarize(g, args)
        return 0
    ing = PyPIIngestor(g, cache_dir=args.cache, dynamic=not args.no_dynamic, report_dir=EVIDENCE / "reports")
    if args.lib:
        if not args.version:
            ap.error("--lib needs --version")
        print(json.dumps(asdict(ing.ingest_version(args.lib, args.version))))
    elif args.ladder:
        for rep in ing.ingest_library(args.ladder):
            print(json.dumps(asdict(rep)), flush=True)
    elif args.gap:
        lib, ver, hint = args.gap
        print(json.dumps(asdict(ing.fill_gap(lib, ver, hint))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
