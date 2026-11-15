"""The iteration driver: the learning curve on GitChameleon (PLAN.md section 4).

iteration 0 : measure TEST with the graph arm, no learning (the static graph).
iteration t : solve TRAIN with the current graph -> run -> learner.record(Episode) for each train problem
              -> learner.maintain() -> measure TEST with the frozen graph -> one curve point.
The learner NEVER sees a test problem (guarded). Every step is recorded in the run folder, so a crashed run resumes
where it stopped: completions/results by row key, learned episodes and maintain() calls in learn.jsonl.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict

from pinned.contract import Episode, LearnReport, Learner, Search

from .arms import GraphArm
from .dataset import Problem
from .pipeline import Tester, solve_stage, test_stage
from .solver import Backoff, CompletionCache, Solver
from .split import Split
from .store import ResultRow, RunStore, context_from_dict, row_key


class LeakError(RuntimeError):
    """A test-split problem was about to reach the learner."""


class GuardedLearner:
    """Wraps a Learner so only TRAIN problems can be recorded."""

    def __init__(self, learner: Learner, split: Split):
        self.inner = learner
        self.train = frozenset(split.train)

    def record(self, episode: Episode) -> LearnReport:
        if episode.problem_id not in self.train:
            raise LeakError(f"problem {episode.problem_id} is not in the train split; the learner must never see it")
        return self.inner.record(episode)

    def maintain(self) -> LearnReport:
        return self.inner.maintain()


class CurveDriver:
    def __init__(self, store: RunStore, problems: list[Problem], split: Split, search: Search, learner: Learner,
                 solver: Solver, cache: CompletionCache, tester: Tester, arm_name: str = "pinned", k: int = 8,
                 concurrency: int = 3, backoff: Backoff | None = None, log: Callable[[str], None] = print,
                 on_step: Callable[[str, int], None] | None = None):
        self.store = store
        self.problems = {p.id: p for p in problems}
        self.split = split
        self.arm = GraphArm(search, arm_name, k)
        self.learner = GuardedLearner(learner, split)
        self.solver, self.cache, self.tester = solver, cache, tester
        self.concurrency, self.backoff, self.log = concurrency, backoff, log
        self.on_step = on_step or (lambda step, it: None)

    def _set(self, name: str) -> list[Problem]:
        return [self.problems[i] for i in self.split.ids(name)]

    def measure(self, iteration: int) -> dict[str, ResultRow]:
        """One curve point: the graph arm on the held-out TEST split, no learning."""
        self.on_step("measure", iteration)
        solve_stage(self.store, self._set("test"), self.arm, "test", iteration, self.solver, self.cache,
                    self.concurrency, self.backoff, self.log)
        return test_stage(self.store, self.problems, self.tester, self.arm.name, "test", iteration, self.log)

    def train(self, iteration: int) -> dict[str, ResultRow]:
        self.on_step("train", iteration)
        solve_stage(self.store, self._set("train"), self.arm, "train", iteration, self.solver, self.cache,
                    self.concurrency, self.backoff, self.log)
        return test_stage(self.store, self.problems, self.tester, self.arm.name, "train", iteration, self.log)

    def learn(self, iteration: int) -> None:
        """record() every train episode of this iteration once, then maintain() once."""
        log = self.store.learn_log()
        recorded = {r["problem"] for r in log if r.get("iteration") == iteration and r.get("op") == "record"}
        maintained = any(r.get("iteration") == iteration and r.get("op") == "maintain" for r in log)
        if maintained:
            return
        comps = self.store.completions()
        results = self.store.results()
        run_id = self.store.run_id
        for pid in self.split.train:
            if pid in recorded:
                continue
            k = row_key(self.arm.name, "train", iteration, pid)
            c, r = comps.get(k), results.get(k)
            if c is None or r is None or c.error:
                continue   # solver failed for this problem: nothing to learn from
            p = self.problems[pid]
            ep = Episode(run_id=run_id, problem_id=pid, iteration=iteration, library=p.library, version=p.version,
                         problem=p.problem, starting_code=p.starting_code, context=context_from_dict(c.context_obj),
                         code=c.code, passed=r.passed, error_type=r.error_type, error_text=r.error_text)
            self.on_step("record", iteration)
            rep = self.learner.record(ep)
            self.store.add_learn({"op": "record", "iteration": iteration, "problem": pid, "passed": r.passed,
                                  "report": asdict(rep)})
        self.on_step("maintain", iteration)
        rep = self.learner.maintain()
        self.store.add_learn({"op": "maintain", "iteration": iteration, "report": asdict(rep)})

    def run(self, iterations: int) -> list[dict]:
        """Iteration 0 (static graph) then `iterations` learning iterations. Returns the curve points."""
        points = [self._point(0, self.measure(0))]
        self.log(f"iteration 0: pass@1 {points[-1]['pass_at_1']:.3f} on test ({points[-1]['n']})")
        for t in range(1, iterations + 1):
            self.train(t)
            self.learn(t)
            points.append(self._point(t, self.measure(t)))
            self.log(f"iteration {t}: pass@1 {points[-1]['pass_at_1']:.3f} on test ({points[-1]['n']})")
        return points

    def _point(self, iteration: int, results: dict[str, ResultRow]) -> dict:
        n = len(results)
        passed = sum(1 for r in results.values() if r.passed)
        return {"iteration": iteration, "n": n, "passed": passed, "pass_at_1": passed / n if n else 0.0,
                "expected": len(self.split.test)}

