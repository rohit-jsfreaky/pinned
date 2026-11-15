"""The `test` stage: run many (problem, code) jobs through the runner in parallel, in each problem's venv."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .answer_key import hidden_test_path
from .dataset import DEFAULT_DATA_DIR, Problem
from .runner import TIMEOUT_S, RunResult, run_candidate
from .venvs import DEFAULT_VENV_DIR, NLTK_DATA_DIR, VenvSpec, is_built, venv_python


@dataclass(frozen=True)
class TestJob:
    __test__ = False      # not a pytest test class
    key: str              # unique within a run, e.g. "none|0|0"
    problem: Problem
    code: str


class MissingVenvs(RuntimeError):
    pass


def check_venvs(problems: Iterable[Problem], venv_dir: Path = DEFAULT_VENV_DIR) -> None:
    missing = sorted({VenvSpec.of(p).dirname for p in problems if not is_built(venv_dir, VenvSpec.of(p))})
    if missing:
        raise MissingVenvs(f"{len(missing)} venvs not built (run `python -m pinned.eval venvs`): {missing[:5]} ...")


def run_tests(jobs: list[TestJob], on_result: Callable[[TestJob, RunResult], None], workers: int = 4,
              venv_dir: Path = DEFAULT_VENV_DIR, data_dir: Path = DEFAULT_DATA_DIR,
              timeout: float = TIMEOUT_S) -> None:
    """Runs every job; `on_result` is called (under a lock, so it may append to a file) as each finishes."""
    check_venvs((j.problem for j in jobs), venv_dir)
    env = {"NLTK_DATA": str(NLTK_DATA_DIR.resolve())}
    lock = threading.Lock()

    def one(job: TestJob) -> RunResult:
        py = venv_python(venv_dir, VenvSpec.of(job.problem))
        return run_candidate(job.problem.id, job.code, py, hidden_test_path(job.problem.id, data_dir), timeout, env)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {pool.submit(one, j): j for j in jobs}
        for fut in as_completed(futs):
            res = fut.result()
            with lock:
                on_result(futs[fut], res)
