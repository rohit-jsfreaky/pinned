"""Iteration driver with fake Search / Learner / solver / tester: step order, the learner never sees TEST problems,
crash + resume without double learning, and the run folder outputs (results, summary, curve)."""

import json

import pytest

from pinned.contract import Context, ContextItem, Episode, LearnReport
from pinned.eval.driver import CurveDriver, GuardedLearner, LeakError
from pinned.eval.execute import TestJob
from pinned.eval.report import build_curve, summarize
from pinned.eval.runner import RunResult
from pinned.eval.solver import CompletionCache, SolverOutput
from pinned.eval.split import make_split
from pinned.eval.store import RunStore
from pinned.graph import count_nodes, upsert_nodes

from eval_fakes import make_problem

PROBLEMS = [make_problem(str(i), lib, type_of_change=("name change" if i % 2 else "argument change"))
            for i, lib in enumerate(["a"] * 6 + ["b"] * 4)]
SPLIT = make_split(PROBLEMS, "sha", 13)


class Events(list):
    pass


class FakeSearch:
    """Context gets 'GOOD' once the learner has learned anything about the library (so the curve can rise)."""

    def __init__(self, events: Events):
        self.events = events
        self.learned: set[str] = set()

    def context_for(self, problem, starting_code, library, version, k=8):
        pid = problem.split(":")[0].removeprefix("Task ")
        self.events.append(("search", pid))
        status = "valid" if library in self.learned else "unknown"
        return Context(library, version, [ContextItem(library, f"{library}.api", status, note="GOOD" if
                                                      library in self.learned else "")])

    def check_calls(self, code, library, version):
        return []

    def explain_api(self, library, path):
        raise NotImplementedError


class FakeLearner:
    def __init__(self, events: Events, search: FakeSearch, crash_after: int | None = None):
        self.events, self.search, self.crash_after = events, search, crash_after
        self.episodes: list[Episode] = []
        self.pending: set[str] = set()

    def record(self, ep: Episode) -> LearnReport:
        if self.crash_after is not None and len(self.episodes) >= self.crash_after:
            raise RuntimeError("simulated crash")
        assert isinstance(ep.context, Context)
        self.episodes.append(ep)
        self.events.append(("record", ep.problem_id))
        self.pending.add(ep.library)
        return LearnReport(edges_reweighted=1)

    def maintain(self) -> LearnReport:
        self.events.append(("maintain", ""))
        self.search.learned |= self.pending   # the graph changes only at maintain time
        return LearnReport(shortcuts_added=1)


class FakeSolver:
    name = "fake"

    def __init__(self):
        self.calls = 0

    def describe(self):
        return {"backend": "fake"}

    def complete(self, prompt):
        self.calls += 1
        return SolverOutput("```python\n" + ("GOOD = 1\n" if "GOOD" in prompt else "BAD = 1\n") + "```", "fake")


class FakeTester:
    def __init__(self, events: Events):
        self.events = events

    def run(self, jobs: list[TestJob], on_result):
        for j in jobs:
            self.events.append(("test", j.problem.id))
            ok = "GOOD" in j.code
            on_result(j, RunResult(ok, "" if ok else "AttributeError", "" if ok else "AttributeError: x"))


def _driver(tmp_path, events, crash_after=None, run_id="curve-test", cache_dir=None):
    search = FakeSearch(events)
    learner = FakeLearner(events, search, crash_after)
    store = RunStore(run_id, tmp_path / "runs")
    solver = FakeSolver()
    d = CurveDriver(store, PROBLEMS, SPLIT, search, learner, solver, CompletionCache(cache_dir or tmp_path / "c"),
                    FakeTester(events), concurrency=1, log=lambda m: None,
                    on_step=lambda step, it: events.append((step, str(it))))
    return d, learner, solver, store


def test_order_and_learner_never_sees_test(tmp_path):
    ev = Events()
    d, learner, _, store = _driver(tmp_path, ev)
    points = d.run(2)
    test_ids, train_ids = set(SPLIT.test), set(SPLIT.train)
    # learner only ever got train problems, each once per learning iteration
    assert {e.problem_id for e in learner.episodes} == train_ids
    assert len(learner.episodes) == 2 * len(train_ids)
    assert not {e.problem_id for e in learner.episodes} & test_ids
    steps = [e for e in ev if e[0] in ("measure", "train", "record", "maintain") and e[1] != ""]
    order = [s for s, _ in steps if s != "record"]
    assert order[:4] == ["measure", "train", "maintain", "measure"]
    # within an iteration: every record before maintain, maintain before that iteration's test measurement
    first_maintain = ev.index(("maintain", ""))
    measure1 = ev.index(("measure", "1"))
    assert all(ev.index(("record", pid)) < first_maintain for pid in train_ids)
    assert first_maintain < measure1
    # iteration 0 searched only test problems before any train problem
    train0 = ev.index(("train", "1"))
    assert {pid for kind, pid in ev[:train0] if kind == "search"} == test_ids
    # the curve rises once the (fake) graph has learned
    assert points[0]["pass_at_1"] == 0.0 and points[1]["pass_at_1"] == 1.0
    assert [p["iteration"] for p in points] == [0, 1, 2]
    # outputs
    results = [json.loads(l) for l in store.results_path.read_text().splitlines()]
    assert {(r["split"], r["iteration"]) for r in results} == {("test", 0), ("train", 1), ("test", 1),
                                                               ("train", 2), ("test", 2)}
    assert set(results[0]) >= {"problem", "arm", "iteration", "passed", "error_type"}
    s = summarize(store, SPLIT)
    g = {(x["arm"], x["iteration"], x["split"]): x for x in s["groups"]}
    assert g[("pinned", 0, "test")]["n"] == len(test_ids) and g[("pinned", 0, "test")]["missing"] == 0
    assert g[("pinned", 2, "test")]["pass_at_1"] == 1.0
    assert set(g[("pinned", 0, "test")]["per_library"]) == {"a", "b"}
    assert set(g[("pinned", 0, "test")]["per_type_of_change"]) == {"name change", "argument change"}
    curve = build_curve(tmp_path / "runs", tmp_path / "curve.json")
    assert [p["pass_at_1"] for p in curve["curves"][0]["points"]] == [0.0, 1.0, 1.0]


def test_crash_and_resume_never_learns_twice(tmp_path):
    ev = Events()
    d, learner, solver, store = _driver(tmp_path, ev, crash_after=2)
    with pytest.raises(RuntimeError, match="simulated crash"):
        d.run(1)
    calls_before = solver.calls
    ev2 = Events()
    d2, learner2, solver2, _ = _driver(tmp_path, ev2)
    d2.run(1)
    learned = [r for r in store.learn_log() if r["op"] == "record"]
    assert len(learned) == len(SPLIT.train)                       # 2 before the crash + the rest after
    assert len({r["problem"] for r in learned}) == len(SPLIT.train)
    assert len(learner2.episodes) == len(SPLIT.train) - 2
    assert sum(1 for r in store.learn_log() if r["op"] == "maintain") == 1
    assert solver2.calls == len(SPLIT.test)   # train completions were reused; only iteration-1 test is new
    assert calls_before == len(SPLIT.test) + len(SPLIT.train)


def test_guarded_learner_rejects_test_problems():
    ev = Events()
    g = GuardedLearner(FakeLearner(ev, FakeSearch(ev)), SPLIT)
    ep = Episode("r", SPLIT.test[0], 1, "a", "1.0", "p", "s", None, "", False)
    with pytest.raises(LeakError):
        g.record(ep)


class GraphEpisodeLearner(FakeLearner):
    """Writes every Episode it records into the REAL HydraDB test node (via pinned.graph helpers)."""

    def __init__(self, events, search, graph, run_key):
        super().__init__(events, search)
        self.graph, self.run_key = graph, run_key

    def record(self, ep):
        rep = super().record(ep)
        upsert_nodes(self.graph, "Episode", [{"key": f"{self.run_key}|{ep.problem_id}|{ep.iteration}",
                                              "run_id": self.run_key, "problem_id": ep.problem_id,
                                              "library": ep.library, "version": ep.version, "passed": ep.passed,
                                              "error_type": ep.error_type, "iteration": ep.iteration}])
        return rep


def test_driver_writes_only_train_episodes_to_hydradb(tmp_path, graph, run_key):
    ev = Events()
    search = FakeSearch(ev)
    learner = GraphEpisodeLearner(ev, search, graph, run_key)
    d = CurveDriver(RunStore("g", tmp_path), PROBLEMS, SPLIT, search, learner, FakeSolver(),
                    CompletionCache(tmp_path / "c"), FakeTester(ev), concurrency=1, log=lambda m: None)
    d.run(1)
    rows = graph.query("MATCH (e:Episode) WHERE e.run_id = $r RETURN e.problem_id AS pid", {"r": run_key})
    assert sorted(r["pid"] for r in rows) == sorted(SPLIT.train)
    assert count_nodes(graph, "Episode") >= len(SPLIT.train)
