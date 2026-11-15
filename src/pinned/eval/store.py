"""A run folder: evidence/runs/<run_id>/ with config.json, completions.jsonl, results.jsonl, summary.json (+ the
driver's learn.jsonl). Append-only JSONL, so every stage is resumable after a crash."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pinned.contract import Context, ContextItem, Hop

RUNS_DIR = Path("evidence/runs")


def row_key(arm: str, split: str, iteration: int, problem_id: str) -> str:
    return f"{arm}|{split}|{iteration}|{problem_id}"


@dataclass
class CompletionRow:
    arm: str
    split: str
    iteration: int
    problem: str
    code: str
    text: str = ""
    error: str = ""                 # solver failure (no code); the test stage skips these rows
    prompt_hash: str = ""
    cache_key: str = ""
    model: str = ""
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    context_chars: int = 0
    context: str = ""               # the context text the solver got (audit)
    context_obj: dict | None = None  # graph arms: the structured Context (rebuilt for Episodes on resume)
    arm_meta: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return row_key(self.arm, self.split, self.iteration, self.problem)


@dataclass
class ResultRow:
    arm: str
    split: str
    iteration: int
    problem: str
    passed: bool
    error_type: str = ""
    error_text: str = ""
    seconds: float = 0.0
    library: str = ""
    version: str = ""
    type_of_change: str = ""

    @property
    def key(self) -> str:
        return row_key(self.arm, self.split, self.iteration, self.problem)


def context_from_dict(d: dict | None) -> Context | None:
    if not d:
        return None
    items = [ContextItem(**{**it, "chain": [Hop(**h) for h in it.get("chain", [])]}) for it in d.get("items", [])]
    return Context(library=d["library"], version=d["version"], items=items, query_ms=d.get("query_ms", 0.0),
                   graph_queries=d.get("graph_queries", 0))


def git_commit() -> str:
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        return res.stdout.strip() if res.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


class RunStore:
    def __init__(self, run_id: str, root: Path = RUNS_DIR):
        if not run_id or "/" in run_id or run_id.startswith("."):
            raise ValueError(f"bad run id {run_id!r}")
        self.run_id = run_id
        self.dir = Path(root) / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.dir / "config.json"
        self.completions_path = self.dir / "completions.jsonl"
        self.results_path = self.dir / "results.jsonl"
        self.summary_path = self.dir / "summary.json"
        self.learn_path = self.dir / "learn.jsonl"

    # ---- config
    def config(self) -> dict:
        return json.loads(self.config_path.read_text()) if self.config_path.exists() else {}

    def write_config(self, cfg: dict) -> dict:
        """First write sets the config; later writes must agree on everything that defines the measurement."""
        old = self.config()
        if old:
            for k in ("run_id", "template_hash", "split", "solver_model_alias", "kind"):
                if k in cfg and k in old and cfg[k] != old[k]:
                    raise ValueError(f"run {self.run_id}: config {k} changed ({old[k]!r} -> {cfg[k]!r}); "
                                     "use a new run id")
            merged = {**cfg, **old}
            merged["arms"] = {**cfg.get("arms", {}), **old.get("arms", {})}
            merged["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            cfg = merged
        else:
            cfg = {"run_id": self.run_id, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "git_commit": git_commit(), **cfg}
        self.config_path.write_text(json.dumps(cfg, indent=1) + "\n")
        return cfg

    # ---- jsonl
    @staticmethod
    def _read(path: Path) -> list[dict]:
        if not path.exists():
            return []
        rows = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue   # a torn last line from a crash; the job simply reruns
        return rows

    @staticmethod
    def _append(path: Path, row: dict) -> None:
        with path.open("a") as f:
            f.write(json.dumps(row) + "\n")

    def completions(self) -> dict[str, CompletionRow]:
        """Latest row per key; rows with a solver error are kept only if no successful row exists."""
        out: dict[str, CompletionRow] = {}
        for r in self._read(self.completions_path):
            c = CompletionRow(**r)
            if c.key in out and not out[c.key].error and c.error:
                continue
            out[c.key] = c
        return out

    def add_completion(self, c: CompletionRow) -> None:
        self._append(self.completions_path, asdict(c))

    def results(self) -> dict[str, ResultRow]:
        return {ResultRow(**r).key: ResultRow(**r) for r in self._read(self.results_path)}

    def add_result(self, r: ResultRow) -> None:
        self._append(self.results_path, asdict(r))

    def learn_log(self) -> list[dict]:
        return self._read(self.learn_path)

    def add_learn(self, row: dict) -> None:
        self._append(self.learn_path, row)
