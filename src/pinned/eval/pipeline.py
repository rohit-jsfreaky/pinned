"""The two stages of measuring one arm on a set of problems, joined by the run folder:

- `solve_stage`: arm context -> prompt -> solver -> completions.jsonl (runs wherever `claude` is logged in);
- `test_stage`: completions.jsonl -> runner in venvs -> results.jsonl (runs where the venvs are).
Both skip work already in the run folder, so either can be re-run after a crash.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from .arms import Arm
from .dataset import Problem
from .execute import TestJob, run_tests
from .runner import TIMEOUT_S, RunResult
from .solver import Backoff, Completion, CompletionCache, SolveRequest, Solver, render_prompt, solve_many
from .store import CompletionRow, ResultRow, RunStore, row_key
from .venvs import DEFAULT_VENV_DIR


CONTEXT_WORKERS = 4


class Tester(Protocol):
    def run(self, jobs: list[TestJob], on_result: Callable[[TestJob, RunResult], None]) -> None: ...


class VenvTester:
    """The real tester: GitChameleon hidden tests in the problem venvs."""

    def __init__(self, workers: int = 4, venv_dir: Path = DEFAULT_VENV_DIR, timeout: float = TIMEOUT_S):
        self.workers, self.venv_dir, self.timeout = workers, venv_dir, timeout

    def run(self, jobs: list[TestJob], on_result: Callable[[TestJob, RunResult], None]) -> None:
        run_tests(jobs, on_result, self.workers, self.venv_dir, timeout=self.timeout)


def solve_stage(store: RunStore, problems: list[Problem], arm: Arm, split: str, iteration: int, solver: Solver,
                cache: CompletionCache, concurrency: int = 3, backoff: Backoff | None = None,
                log=print) -> dict[str, CompletionRow]:
    """Completions for every problem (cached ones are free). Returns this stage's rows keyed by row key."""
    have = store.completions()
    todo: list[tuple[Problem, SolveRequest, object]] = []
    out: dict[str, CompletionRow] = {}
    need: list[Problem] = []
    for p in problems:
        k = row_key(arm.name, split, iteration, p.id)
        if k in have and not have[k].error:
            out[k] = have[k]
            continue
        need.append(p)
    # Contexts are built in parallel: a graph arm spends ~1 s per problem in search, which done one by one
    # dominated a whole stage. Order (and so the prompts) is unchanged. Capped at CONTEXT_WORKERS: 20 parallel
    # searches on the full graph (1M Api nodes) pushed a CUES read past HydraDB's 30 s query timeout.
    with ThreadPoolExecutor(max_workers=max(1, min(concurrency, CONTEXT_WORKERS))) as pool:
        ctxs = list(pool.map(arm.context, need))
    for p, actx in zip(need, ctxs):
        req = SolveRequest(arm.name, p.id, iteration, render_prompt(p, actx.text))
        todo.append((p, req, actx))
    if not todo:
        return out
    log(f"solve {arm.name} split={split} it={iteration}: {len(out)} done, {len(todo)} to solve")
    by_req = {id(req): (p, actx) for p, req, actx in todo}
    n_done = [0]

    def on_done(req: SolveRequest, comp: Completion | None, err: str) -> None:
        p, actx = by_req[id(req)]
        row = CompletionRow(
            arm=arm.name, split=split, iteration=iteration, problem=p.id,
            code=comp.code if comp else "", text=comp.text if comp else "", error=err,
            prompt_hash=req.prompt_hash, cache_key=req.key, model=comp.model if comp else "",
            cost_usd=comp.cost_usd if comp else 0.0, input_tokens=comp.input_tokens if comp else 0,
            output_tokens=comp.output_tokens if comp else 0, seconds=comp.seconds if comp else 0.0,
            context_chars=len(actx.text), context=actx.text,
            context_obj=asdict(actx.context) if actx.context is not None else None, arm_meta=actx.meta,
        )
        store.add_completion(row)
        out[row.key] = row
        n_done[0] += 1
        if err:
            log(f"  [{n_done[0]}/{len(todo)}] {p.id}: {err}")
        elif n_done[0] % 20 == 0 or n_done[0] == len(todo):
            log(f"  [{n_done[0]}/{len(todo)}] solved")

    solve_many([req for _, req, _ in todo], solver, cache, on_done, concurrency, backoff, log=log)
    return out


def test_stage(store: RunStore, problems: dict[str, Problem], tester: Tester, arm: str | None = None,
               split: str | None = None, iteration: int | None = None, log=print) -> dict[str, ResultRow]:
    """Run every completion (matching the filters) that has no result yet."""
    comps = store.completions()
    results = store.results()
    jobs = []
    for k, c in comps.items():
        if (arm is not None and c.arm != arm) or (split is not None and c.split != split) \
                or (iteration is not None and c.iteration != iteration):
            continue
        if c.error or k in results:
            continue
        jobs.append(TestJob(k, problems[c.problem], c.code))
    if jobs:
        log(f"test: {len(jobs)} completions to run")
    meta = {k: comps[k] for k in (j.key for j in jobs)}
    count = [0]

    def on_result(job: TestJob, r: RunResult) -> None:
        c = meta[job.key]
        p = job.problem
        row = ResultRow(c.arm, c.split, c.iteration, p.id, r.passed, r.error_type, r.error_text, r.seconds,
                        p.library, p.version, p.type_of_change)
        store.add_result(row)
        results[row.key] = row
        count[0] += 1
        if count[0] % 25 == 0 or count[0] == len(jobs):
            log(f"  [{count[0]}/{len(jobs)}] tested")

    tester.run(jobs, on_result)
    return {k: v for k, v in results.items()
            if (arm is None or v.arm == arm) and (split is None or v.split == split)
            and (iteration is None or v.iteration == iteration)}
