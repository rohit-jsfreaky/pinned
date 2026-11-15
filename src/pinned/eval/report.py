"""summary.json per run (pass@1 per arm x iteration x split, per library, per type_of_change) and evidence/curve.json
built from all runs. Numbers come only from results.jsonl rows."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .split import Split
from .store import RUNS_DIR, ResultRow, RunStore

CURVE_PATH = Path("evidence/curve.json")
BASELINE_ARMS = ("none", "docs")


def _rate(passed: int, n: int) -> float:
    return round(passed / n, 4) if n else 0.0


def _group(rows: list[ResultRow], attr: str) -> dict[str, dict]:
    acc: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in rows:
        a = acc[getattr(r, attr) or "?"]
        a[0] += int(r.passed)
        a[1] += 1
    return {k: {"passed": v[0], "n": v[1], "pass_at_1": _rate(v[0], v[1])} for k, v in sorted(acc.items())}


def summarize(store: RunStore, split: Split | None = None) -> dict:
    results = list(store.results().values())
    comps = store.completions()
    cells: dict[tuple[str, int, str], list[ResultRow]] = defaultdict(list)
    for r in results:
        cells[(r.arm, r.iteration, r.split)].append(r)
    groups = []
    for (arm, it, sp), rows in sorted(cells.items()):
        passed = sum(int(r.passed) for r in rows)
        expected = len(split.ids(sp)) if split is not None and sp in ("train", "test") else None
        c_rows = [c for c in comps.values() if c.arm == arm and c.iteration == it and c.split == sp]
        solver_failed = sorted(c.problem for c in c_rows if c.error)
        groups.append({
            "arm": arm, "iteration": it, "split": sp, "n": len(rows), "passed": passed,
            "pass_at_1": _rate(passed, len(rows)),
            "expected": expected,
            "missing": (expected - len(rows)) if expected is not None else None,
            "solver_failed": solver_failed,
            "pass_at_1_over_expected": _rate(passed, expected) if expected else None,
            "error_types": dict(Counter(r.error_type for r in rows if not r.passed).most_common()),
            "per_library": _group(rows, "library"),
            "per_type_of_change": _group(rows, "type_of_change"),
            "solver_cost_usd": round(sum(c.cost_usd for c in c_rows), 4),
            "models": sorted({m for c in c_rows for m in c.model.split(",") if m}),
        })
    summary = {"run_id": store.run_id, "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "metric": "pass@1 (one completion per problem; hidden tests)", "groups": groups}
    store.summary_path.write_text(json.dumps(summary, indent=1) + "\n")
    return summary


def build_curve(runs_dir: Path = RUNS_DIR, out: Path = CURVE_PATH) -> dict:
    """Baselines = the latest run of each baseline arm on TEST; curves = every graph arm's TEST points by iteration."""
    baselines: dict[str, dict] = {}
    curves: dict[str, dict] = {}
    for sp in sorted(Path(runs_dir).glob("*/summary.json")):
        s = json.loads(sp.read_text())
        cfg_path = sp.parent / "config.json"
        created = json.loads(cfg_path.read_text()).get("created", "") if cfg_path.exists() else ""
        for g in s.get("groups", []):
            if g["split"] != "test":
                continue
            point = {"iteration": g["iteration"], "n": g["n"], "passed": g["passed"], "pass_at_1": g["pass_at_1"],
                     "expected": g.get("expected")}
            if g["arm"] in BASELINE_ARMS:
                prev = baselines.get(g["arm"])
                if prev is None or created >= prev["created"]:
                    baselines[g["arm"]] = {**point, "run_id": s["run_id"], "created": created}
            else:
                c = curves.setdefault(f"{s['run_id']}:{g['arm']}", {"run_id": s["run_id"], "arm": g["arm"],
                                                                     "points": []})
                c["points"].append(point)
    for c in curves.values():
        c["points"].sort(key=lambda p: p["iteration"])
    curve = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "metric": "pass@1 on the held-out TEST split (evidence/split.json)",
             "baselines": baselines, "curves": list(curves.values())}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(curve, indent=1) + "\n")
    return curve
