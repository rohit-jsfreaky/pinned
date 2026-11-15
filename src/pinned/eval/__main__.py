"""`uv run python -m pinned.eval <command>` — every step of the GitChameleon harness.

  download                     HF dataset -> data/gitchameleon/ (+ dataset sha)
  split                        write evidence/split.json ONCE (50/50 by library, seed 13)
  venvs                        build every deduped venv -> evidence/harness/venvs.json
  selfcheck                    ground truth through the runner -> evidence/harness/selfcheck.json
  check-solver                 does `claude -p` work here? -> evidence/harness/solver-check.json
  fetch-docs                   fetch + cache the docs-arm pages -> evidence/harness/docs-fetch.json
  solve  --run-id --arm        completions only (laptop side if claude only works there)
  test   --run-id              run completions in venvs (cloud side)
  baseline --run-id --arm      solve + test + summarize in one go
  summarize --run-id           summary.json for a run
  curve-json                   evidence/curve.json from all runs
  curve  --run-id --iterations the learning curve (needs HydraDB + pinned.search / pinned.learn)
         [--arm-kind graph|plain|shuffled]  ablation (plain store) / negative control (shuffled ranking)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from . import dataset as ds
from .arms import DOCS_BUDGET_CHARS, Arm, DocsArm, GraphArm, NoneArm
from .docs import DEFAULT_DOCS_CACHE, DocsFetcher
from .pipeline import VenvTester, solve_stage, test_stage
from .report import build_curve, summarize
from .runner import TIMEOUT_S
from .solver import (
    TEMPLATE_HASH,
    Backoff,
    ClaudeCLISolver,
    CompletionCache,
    SolverError,
    extract_code,
)
from .split import SPLIT_PATH, load_split, make_split, write_split_once
from .store import RunStore, git_commit
from .venvs import (
    DEFAULT_VENV_DIR,
    ERA_CONSTRAINTS,
    PYTHON_VERSIONS,
    VenvSpec,
    build_all,
    specs_for,
    total_disk_bytes,
)

HARNESS = Path("evidence/harness")


def _log(msg: str) -> None:
    print(msg, flush=True)


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1) + "\n")
    _log(f"wrote {path}")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------- setup commands
def cmd_download(a) -> None:
    sha = ds.download(Path(a.data_dir), a.revision)
    _log(f"dataset {ds.REPO_ID} @ {sha} -> {a.data_dir} ({len(ds.load_problems(Path(a.data_dir)))} problems)")


def cmd_split(a) -> None:
    probs = ds.load_problems(Path(a.data_dir))
    s = make_split(probs, ds.dataset_sha(Path(a.data_dir)), a.seed)
    write_split_once(s, Path(a.out))
    _log(f"{a.out}: train {len(s.train)} / test {len(s.test)} (seed {s.seed}, dataset {s.dataset_sha[:12]})")


def cmd_venvs(a) -> None:
    probs = ds.load_problems(Path(a.data_dir))
    specs = list(specs_for(probs).values())
    _log(f"{len(specs)} venvs for {len(probs)} problems -> {a.venv_dir}")
    t0 = time.monotonic()
    results = build_all(specs, Path(a.venv_dir), workers=a.workers, rebuild=a.rebuild, log=_log)
    wall = round(time.monotonic() - t0, 1)
    ok = [r for r in results if r.ok]
    users: dict[str, list[str]] = {}
    for p in probs:
        users.setdefault(VenvSpec.of(p).key, []).append(p.id)
    report = {
        "created": _now(), "n_venvs": len(specs), "ok": len(ok), "failed": len(specs) - len(ok),
        "method": {"reference": "mrcabbage972/GitChameleonBenchmark gitchameleon/create_venvs.py @ 3a1b604",
                   "installer": "uv pip (hard-linked from one cache)", "pythons": PYTHON_VERSIONS,
                   "torch": "CPU-only wheels from download.pytorch.org/whl/cpu",
                   "era_constraints": {f"{k[0]}=={k[1]}": list(v) for k, v in ERA_CONSTRAINTS.items()}},
        "wall_seconds_this_invocation": wall,
        "build_seconds_sum": round(sum(r.seconds for r in results), 1),
        "apparent_bytes_sum": sum(r.bytes for r in results),
        "disk_bytes_total": total_disk_bytes(Path(a.venv_dir)),
        "venvs": [{"key": r.key, **r.spec, "ok": r.ok, "seconds": r.seconds, "bytes": r.bytes,
                   "reused": r.skipped, "problems": users.get(r.key, []), "error": r.error,
                   "key_packages": {k: v for k, v in r.packages.items()
                                    if k in {r.spec["library"].lower(), "numpy", "scipy", "pytest", "torch"}}}
                  for r in results],
    }
    _write(HARNESS / "venvs.json", report)
    if report["failed"]:
        sys.exit(f"{report['failed']} venvs failed")


def cmd_selfcheck(a) -> None:
    from .selfcheck import run_selfcheck

    rep = run_selfcheck(ds.load_problems(Path(a.data_dir)), a.workers, Path(a.venv_dir), Path(a.data_dir),
                        fresh=a.fresh, timeout=a.timeout, log=_log)
    _log(f"self-check: {rep['passed']}/{rep['n']} pass ({rep['pass_rate']:.2%}); failures by type {rep['error_types']}")


def cmd_check_solver(a) -> None:
    solver = ClaudeCLISolver(model=a.model)
    rec: dict = {"created": _now(), "host": os.uname().nodename if hasattr(os, "uname") else ""}
    try:
        rec["solver"] = solver.describe()
        out = solver.complete("Write a Python function add(a, b) that returns a + b. "
                              "Reply with a single ```python code block.")
        code = extract_code(out.text)
        ns: dict = {}
        exec(compile(code, "<solver-check>", "exec"), ns)   # our own trivial prompt, not dataset content
        rec.update({"works": ns["add"](2, 3) == 5, "model": out.model, "seconds": out.seconds,
                    "cost_usd": out.cost_usd, "reply": out.text})
    except (SolverError, OSError, SyntaxError, KeyError) as e:
        rec.update({"works": False, "error": f"{type(e).__name__}: {e}"})
    _write(HARNESS / "solver-check.json", rec)
    _log(f"claude -p works here: {rec['works']}")


def cmd_fetch_docs(a) -> None:
    probs = ds.load_problems(Path(a.data_dir))
    if a.split != "all":
        ids = set(load_split(Path(a.split_path)).ids(a.split))
        probs = [p for p in probs if p.id in ids]
    fetcher = DocsFetcher(Path(a.docs_cache))
    urls = sorted({u for p in probs for u in p.docs})
    pages = {}
    for i, u in enumerate(urls, 1):
        pages[u] = fetcher.get(u)
        if i % 20 == 0 or i == len(urls):
            _log(f"  [{i}/{len(urls)}] fetched")
    fetcher.close()
    arm = DocsArm(DocsFetcher(Path(a.docs_cache), offline=True), a.budget)
    with_text = [p.id for p in probs if arm.context(p).text]
    rep = {"created": _now(), "split": a.split, "problems": len(probs), "urls": len(urls),
           "ok": sum(1 for p in pages.values() if p.ok and p.text), "budget_chars": a.budget,
           "problems_with_docs_context": len(with_text),
           "failed": [{"url": u, "status": p.status, "error": p.error} for u, p in pages.items()
                      if not (p.ok and p.text)]}
    _write(HARNESS / f"docs-fetch-{a.split}.json", rep)


# ---------------------------------------------------------------- run commands
def _arm(name: str, a) -> Arm:
    if name == "none":
        return NoneArm()
    if name == "docs":
        return DocsArm(DocsFetcher(Path(a.docs_cache), offline=getattr(a, "offline_docs", False)), a.budget)
    if name in ("pinned", "graph"):
        search = _graph_search()
        return GraphArm(search, name)
    raise SystemExit(f"unknown arm {name!r} (none | docs | pinned)")


def _graph_search():
    from pinned.graph import HydraGraph

    try:
        from pinned.search import GraphSearch
    except ImportError as e:
        raise SystemExit(f"the graph arm needs pinned.search.GraphSearch (part C): {e}") from e
    return GraphSearch(HydraGraph())


def _run_config(a, arm: Arm, solver: ClaudeCLISolver | None, kind: str) -> dict:
    split = load_split(Path(a.split_path))
    cfg = {
        "kind": kind,
        "dataset": ds.REPO_ID, "dataset_sha": split.dataset_sha,
        "split": {"path": str(a.split_path), "seed": split.seed, "train": len(split.train), "test": len(split.test)},
        "template_hash": TEMPLATE_HASH,
        "runner": {"timeout_s": TIMEOUT_S, "venv_dir": str(DEFAULT_VENV_DIR)},
        "arms": {arm.name: arm.describe()},
    }
    if solver is not None:
        cfg["solver"] = solver.describe()
        cfg["solver_model_alias"] = solver.model
    return cfg


def _problems_for(a) -> list[ds.Problem]:
    probs = ds.load_problems(Path(a.data_dir))
    ids = load_split(Path(a.split_path)).ids(a.split)
    by = ds.by_id(probs)
    chosen = [by[i] for i in ids]
    if a.limit:
        chosen = chosen[: a.limit]
    return chosen


def cmd_solve(a) -> None:
    store = RunStore(a.run_id)
    arm = _arm(a.arm, a)
    solver = ClaudeCLISolver(model=a.model)
    store.write_config(_run_config(a, arm, solver, "baseline" if a.arm in ("none", "docs") else "arm"))
    rows = solve_stage(store, _problems_for(a), arm, a.split, a.iteration, solver, CompletionCache(Path(a.cache)),
                       a.concurrency, Backoff(), _log)
    failed = [r.problem for r in rows.values() if r.error]
    _record_models(store)
    _log(f"solve done: {len(rows) - len(failed)} completions, {len(failed)} solver failures -> {store.dir}")


def _record_models(store: RunStore) -> None:
    models = sorted({m for c in store.completions().values() for m in c.model.split(",") if m})
    store.update_config(solver_model_ids=models)


def _record_runner(store: RunStore) -> None:
    store.update_config(runner={"timeout_s": TIMEOUT_S, "venv_dir": str(DEFAULT_VENV_DIR),
                                "network": "offline (proxy env -> closed local port)",
                                "venvs_evidence": "evidence/harness/venvs.json",
                                "selfcheck_evidence": "evidence/harness/selfcheck.json",
                                "tested_at_commit": git_commit()})


def cmd_test(a) -> None:
    store = RunStore(a.run_id)
    _record_runner(store)
    probs = ds.by_id(ds.load_problems(Path(a.data_dir)))
    test_stage(store, probs, VenvTester(a.workers), a.arm, a.split, a.iteration, _log)
    s = summarize(store, load_split(Path(a.split_path)))
    for g in s["groups"]:
        _log(f"{g['arm']} it={g['iteration']} {g['split']}: pass@1 {g['pass_at_1']:.3f} ({g['passed']}/{g['n']})")


def cmd_baseline(a) -> None:
    cmd_solve(a)
    store = RunStore(a.run_id)
    _record_runner(store)
    probs = ds.by_id(ds.load_problems(Path(a.data_dir)))
    test_stage(store, probs, VenvTester(a.workers), a.arm, a.split, a.iteration, _log)
    s = summarize(store, load_split(Path(a.split_path)))
    for g in s["groups"]:
        _log(f"{g['arm']} it={g['iteration']} {g['split']}: pass@1 {g['pass_at_1']:.3f} ({g['passed']}/{g['n']}) "
             f"missing={g['missing']}")
    build_curve()


def cmd_summarize(a) -> None:
    s = summarize(RunStore(a.run_id), load_split(Path(a.split_path)))
    _log(json.dumps([{k: g[k] for k in ("arm", "iteration", "split", "n", "passed", "pass_at_1")}
                     for g in s["groups"]], indent=1))


def cmd_curve_json(a) -> None:
    c = build_curve()
    _log(f"baselines: { {k: v['pass_at_1'] for k, v in c['baselines'].items()} }; curves: {len(c['curves'])}")


def cmd_curve(a) -> None:
    from pinned.graph import HydraGraph

    from .driver import CurveDriver

    try:
        from pinned.learn import GraphLearner
        from pinned.search import GraphSearch
    except ImportError as e:
        raise SystemExit(f"the curve needs pinned.search.GraphSearch and pinned.learn.GraphLearner: {e}") from e
    g = HydraGraph()
    problems = ds.load_problems(Path(a.data_dir))
    search, learner, arm_info = curve_parts(a.arm_kind, g, a, problems, GraphSearch, GraphLearner)
    store = RunStore(a.run_id)
    solver = ClaudeCLISolver(model=a.model)
    arm = GraphArm(search, a.arm_name, a.k)
    cfg = _run_config(a, arm, solver, "curve")
    cfg["iterations"] = a.iterations
    cfg["arm_kind"] = arm_info
    store.write_config(cfg)
    split = load_split(Path(a.split_path))
    drv = CurveDriver(store, problems, split, search, learner, solver,
                      CompletionCache(Path(a.cache)), VenvTester(a.workers), a.arm_name, a.k, a.concurrency,
                      Backoff(), _log)
    _record_runner(store)
    drv.run(a.iterations)
    _record_models(store)
    summarize(store, split)
    build_curve()


ARM_KINDS = ("graph", "plain", "shuffled")
SHUFFLE_SEED = 13


def curve_parts(kind: str, g, a, problems: list[ds.Problem], search_cls, learner_cls):
    """(search, learner, description) for one curve arm. Every arm uses the same solver / template / split.
      graph     GraphSearch + GraphLearner on HydraDB (the product)
      plain     ablation: PlainStore (same facts as plain dicts, no traversal) is BOTH search and learner
      shuffled  negative control: ShuffledSearch(GraphSearch, seed 13) + GraphLearner"""
    if kind == "graph":
        return (search_cls(g), learner_cls(g, ingestor=_ingestor(g, a), run_id=a.run_id, split_path=str(a.split_path)),
                {"kind": "graph"})
    if kind == "plain":
        from pinned.learn.plain import PlainStore

        libs = sorted({p.library for p in problems})
        t0 = time.monotonic()
        store = PlainStore.from_graph(g, libs, split_path=str(a.split_path))
        secs = round(time.monotonic() - t0, 1)
        _log(f"plain store: exported {len(store.libs)} libraries from the graph in {secs}s")
        return store, store, {"kind": "plain", "libraries": libs, "export_seconds": secs,
                              "note": "PlainStore.from_graph: same facts as plain dicts; no traversal; learns in-memory"}
    if kind == "shuffled":
        from pinned.learn.shuffled import ShuffledSearch

        search = ShuffledSearch(search_cls(g), seed=SHUFFLE_SEED)
        learner = learner_cls(g, ingestor=_ingestor(g, a), run_id=a.run_id, split_path=str(a.split_path))
        return search, learner, {"kind": "shuffled", "seed": SHUFFLE_SEED, "mode": search.mode,
                                 "inner": type(search.inner).__name__}
    raise SystemExit(f"unknown --arm-kind {kind!r} ({' | '.join(ARM_KINDS)})")


def _ingestor(g, a):
    if a.no_ingest:
        return None
    try:
        from pinned.ingest import PyPIIngestor
    except ImportError:
        _log("pinned.ingest not available: gap-driven ingestion disabled for this run")
        return None
    return PyPIIngestor(g, cache_dir=Path("data/ingest_cache"))


# ---------------------------------------------------------------- argparse
def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m pinned.eval", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=str(ds.DEFAULT_DATA_DIR))
    ap.add_argument("--split-path", default=str(SPLIT_PATH))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("download")
    p.add_argument("--revision", default=None)
    p.set_defaults(fn=cmd_download)

    p = sub.add_parser("split")
    p.add_argument("--seed", type=int, default=13)
    p.add_argument("--out", default=str(SPLIT_PATH))
    p.set_defaults(fn=cmd_split)

    p = sub.add_parser("venvs")
    p.add_argument("--venv-dir", default=str(DEFAULT_VENV_DIR))
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--rebuild", action="store_true")
    p.set_defaults(fn=cmd_venvs)

    p = sub.add_parser("selfcheck")
    p.add_argument("--venv-dir", default=str(DEFAULT_VENV_DIR))
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--timeout", type=float, default=TIMEOUT_S)
    p.add_argument("--fresh", action="store_true", help="ignore cached self-check results")
    p.set_defaults(fn=cmd_selfcheck)

    p = sub.add_parser("check-solver")
    p.add_argument("--model", default="haiku")
    p.set_defaults(fn=cmd_check_solver)

    p = sub.add_parser("fetch-docs")
    p.add_argument("--split", default="test", choices=["train", "test", "all"])
    p.add_argument("--docs-cache", default=str(DEFAULT_DOCS_CACHE))
    p.add_argument("--budget", type=int, default=DOCS_BUDGET_CHARS)
    p.set_defaults(fn=cmd_fetch_docs)

    def run_args(p: argparse.ArgumentParser, solve: bool, test: bool) -> None:
        p.add_argument("--run-id", required=True)
        p.add_argument("--split", default="test", choices=["train", "test", "all"])
        p.add_argument("--iteration", type=int, default=0)
        if solve:
            p.add_argument("--arm", required=True, choices=["none", "docs", "pinned"])
            p.add_argument("--model", default="haiku")
            p.add_argument("--concurrency", type=int, default=3)
            p.add_argument("--cache", default="data/eval_cache/completions")
            p.add_argument("--docs-cache", default=str(DEFAULT_DOCS_CACHE))
            p.add_argument("--budget", type=int, default=DOCS_BUDGET_CHARS)
            p.add_argument("--offline-docs", action="store_true", help="docs arm: use only cached pages")
            p.add_argument("--limit", type=int, default=0, help="first N problems of the split (smoke runs)")
        else:
            p.add_argument("--arm", default=None)
        if test:
            p.add_argument("--workers", type=int, default=4)

    p = sub.add_parser("solve")
    run_args(p, solve=True, test=False)
    p.set_defaults(fn=cmd_solve)
    p = sub.add_parser("test")
    run_args(p, solve=False, test=True)
    p.set_defaults(fn=cmd_test)
    p = sub.add_parser("baseline")
    run_args(p, solve=True, test=True)
    p.set_defaults(fn=cmd_baseline)

    p = sub.add_parser("summarize")
    p.add_argument("--run-id", required=True)
    p.set_defaults(fn=cmd_summarize)
    p = sub.add_parser("curve-json")
    p.set_defaults(fn=cmd_curve_json)

    p = sub.add_parser("curve")
    p.add_argument("--run-id", required=True)
    p.add_argument("--iterations", type=int, default=3)
    p.add_argument("--arm-name", default="pinned")
    p.add_argument("--arm-kind", default="graph", choices=list(ARM_KINDS),
                   help="graph (default) | plain (ablation: PlainStore) | shuffled (negative control)")
    p.add_argument("--k", type=int, default=8)
    p.add_argument("--model", default="haiku")
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--cache", default="data/eval_cache/completions")
    p.add_argument("--no-ingest", action="store_true", help="learner without gap-driven ingestion")
    p.set_defaults(fn=cmd_curve)

    a = ap.parse_args(argv)
    ds.ensure_data_guard()
    a.fn(a)


if __name__ == "__main__":
    main()
