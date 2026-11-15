"""Runner self-check: every ground-truth solution must pass its hidden test in our venvs (proof the harness is right).
The ONLY use of the ground-truth solutions. Results never touch a prompt or the graph; the evidence file keeps only
error types and messages (no solution code)."""

from __future__ import annotations

import json
import platform
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .answer_key import ground_truth
from .dataset import DEFAULT_DATA_DIR, Problem, dataset_sha
from .execute import TestJob, run_tests
from .runner import TIMEOUT_S, RunResult
from .venvs import DEFAULT_VENV_DIR, VenvSpec

EVIDENCE = Path("evidence/harness/selfcheck.json")
PARTIAL = Path("data/selfcheck/partial.jsonl")


def run_selfcheck(problems: list[Problem], workers: int = 4, venv_dir: Path = DEFAULT_VENV_DIR,
                  data_dir: Path = DEFAULT_DATA_DIR, out: Path = EVIDENCE, partial: Path = PARTIAL,
                  timeout: float = TIMEOUT_S, fresh: bool = False, log=print) -> dict:
    gt = ground_truth(data_dir)
    partial.parent.mkdir(parents=True, exist_ok=True)
    if fresh and partial.exists():
        partial.unlink()
    done: dict[str, dict] = {}
    if partial.exists():
        for line in partial.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                done[row["id"]] = row
    jobs = [TestJob(p.id, p, gt[p.id]) for p in problems if p.id not in done]
    log(f"self-check: {len(done)} cached, {len(jobs)} to run")
    t0 = time.monotonic()
    with partial.open("a") as f:
        def on_result(job: TestJob, r: RunResult) -> None:
            row = {"id": job.problem.id, "passed": r.passed, "error_type": r.error_type,
                   "error": r.error_text.splitlines()[-1] if r.error_text else "", "seconds": r.seconds}
            done[job.problem.id] = row
            f.write(json.dumps(row) + "\n")
            f.flush()
            if not r.passed:
                log(f"  FAIL {job.problem.id} {job.problem.library}=={job.problem.version}: {row['error']}")
        run_tests(jobs, on_result, workers, venv_dir, data_dir, timeout)
    wall = round(time.monotonic() - t0, 1)
    by = {p.id: p for p in problems}
    rows = [done[p.id] for p in problems]
    failures = [{**r, "library": by[r["id"]].library, "version": by[r["id"]].version,
                 "python_version": by[r["id"]].python_version, "venv": VenvSpec.of(by[r["id"]]).dirname}
                for r in rows if not r["passed"]]
    per_lib: dict[str, list[int]] = {}
    for r in rows:
        acc = per_lib.setdefault(by[r["id"]].library, [0, 0])
        acc[0] += int(r["passed"])
        acc[1] += 1
    passed = sum(int(r["passed"]) for r in rows)
    report = {
        "what": "ground-truth solutions through the Pinned runner (expect ~100% pass)",
        "dataset_sha": dataset_sha(data_dir),
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": {"platform": platform.platform(), "python": platform.python_version()},
        "runner": {"timeout_s": timeout, "workers": workers, "venv_dir": str(venv_dir)},
        "n": len(rows), "passed": passed, "pass_rate": round(passed / len(rows), 4) if rows else 0.0,
        "wall_seconds_this_invocation": wall,
        "test_seconds_sum": round(sum(r["seconds"] for r in rows), 1),
        "error_types": dict(Counter(r["error_type"] for r in rows if not r["passed"])),
        "per_library": {k: {"passed": v[0], "n": v[1]} for k, v in sorted(per_lib.items())},
        "failures": failures,
        "per_problem": {r["id"]: {"passed": r["passed"], "error_type": r["error_type"], "seconds": r["seconds"]}
                        for r in rows},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1) + "\n")
    return report
