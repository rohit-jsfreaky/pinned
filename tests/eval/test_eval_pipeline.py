"""solve and test as separate stages joined by the run folder (laptop solves, cloud tests); resume after a torn line."""

from pinned.eval.arms import NoneArm
from pinned.eval import pipeline
from pinned.eval.pipeline import solve_stage
from pinned.eval.report import build_curve, summarize
from pinned.eval.runner import RunResult
from pinned.eval.solver import CompletionCache, SolverOutput
from pinned.eval.split import make_split
from pinned.eval.store import RunStore

from eval_fakes import make_problem

PROBLEMS = [make_problem(str(i), "a" if i < 4 else "b") for i in range(6)]


class Solver:
    name = "fake"

    def __init__(self):
        self.calls = 0

    def describe(self):
        return {}

    def complete(self, prompt):
        self.calls += 1
        return SolverOutput("```python\nok = True\n```", "fake")


class FakeTester:
    def __init__(self):
        self.ran = []

    def run(self, jobs, on_result):
        for j in jobs:
            self.ran.append(j.key)
            on_result(j, RunResult(int(j.problem.id) % 2 == 0, "" if int(j.problem.id) % 2 == 0 else "TypeError"))


def test_solve_then_test_in_separate_processes(tmp_path):
    runs = tmp_path / "runs"
    solver = Solver()
    # "laptop": solve only
    rows = solve_stage(RunStore("b1", runs), PROBLEMS, NoneArm(), "test", 0, solver, CompletionCache(tmp_path / "c"),
                       concurrency=2, log=lambda m: None)
    assert len(rows) == 6 and solver.calls == 6
    assert not (runs / "b1" / "results.jsonl").exists()
    # simulate a crash that tore the last completions line
    with (runs / "b1" / "completions.jsonl").open("a") as f:
        f.write('{"arm": "none", "spl')
    # "cloud": a fresh store object reads the completions and tests them
    tester = FakeTester()
    res = pipeline.test_stage(RunStore("b1", runs), {p.id: p for p in PROBLEMS}, tester, log=lambda m: None)
    assert len(res) == 6 and sum(r.passed for r in res.values()) == 3
    # rerunning both stages does nothing new
    solve_stage(RunStore("b1", runs), PROBLEMS, NoneArm(), "test", 0, solver, CompletionCache(tmp_path / "c"),
                log=lambda m: None)
    pipeline.test_stage(RunStore("b1", runs), {p.id: p for p in PROBLEMS}, tester, log=lambda m: None)
    assert solver.calls == 6 and len(tester.ran) == 6
    split = make_split(PROBLEMS, "sha")
    s = summarize(RunStore("b1", runs), split)
    assert s["groups"][0]["pass_at_1"] == 0.5 and s["groups"][0]["error_types"] == {"TypeError": 3}
    curve = build_curve(runs, tmp_path / "curve.json")
    assert curve["baselines"]["none"]["pass_at_1"] == 0.5 and curve["baselines"]["none"]["run_id"] == "b1"


def test_config_guards_measurement_fields(tmp_path):
    st = RunStore("cfg", tmp_path)
    st.write_config({"template_hash": "aaa", "arms": {"none": {}}})
    st.write_config({"template_hash": "aaa", "arms": {"docs": {}}})
    assert set(st.config()["arms"]) == {"none", "docs"}
    try:
        st.write_config({"template_hash": "bbb"})
    except ValueError as e:
        assert "template_hash" in str(e)
    else:
        raise AssertionError("a changed template must need a new run id")
