"""GitChameleon 2.0: download, load, and the solver-visible view of a problem.

`Problem` carries ONLY what the solver may see plus metadata used for reporting. The answer key (`solution`,
`ground_truth_solutions.jsonl`, `hidden_tests/`) is never loaded here; see `answer_key.py` (runner self-check only).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

REPO_ID = "cabbage972/GitChameleon-2.0"
DEFAULT_DATA_DIR = Path("data/gitchameleon")
SHA_FILE = "DATASET_SHA"

# Fields the solver may see (PLAN.md section 8). `docs` only reaches a prompt through the labelled `docs` arm.
SOLVER_FIELDS = ("problem", "starting_code", "library", "version", "python_version", "additional_dependencies")


@dataclass(frozen=True)
class Problem:
    id: str                          # example_id, e.g. "0"
    library: str
    version: str
    python_version: str
    additional_dependencies: str
    problem: str
    starting_code: str
    type_of_change: str = ""         # reporting only
    docs: tuple[str, ...] = field(default_factory=tuple)   # docs arm only

    @classmethod
    def from_row(cls, row: dict) -> "Problem":
        return cls(
            id=str(row["example_id"]),
            library=str(row["library"]).strip().lower(),
            version=str(row["version"]).strip(),
            python_version=str(row["python_version"]).strip(),
            additional_dependencies=str(row.get("additional_dependencies") or "").strip(),
            problem=str(row["problem"]),
            starting_code=str(row["starting_code"]),
            type_of_change=str(row.get("type_of_change") or ""),
            docs=tuple(str(u) for u in (row.get("docs") or [])),
        )

    def solver_view(self) -> dict[str, str]:
        """Exactly the fields the solver is allowed to see."""
        return {f: getattr(self, f) for f in SOLVER_FIELDS}


def download(data_dir: Path = DEFAULT_DATA_DIR, revision: str | None = None) -> str:
    """Download the full HF dataset snapshot into `data_dir`; returns (and records) the dataset commit sha."""
    from huggingface_hub import HfApi, snapshot_download

    sha = revision or HfApi().dataset_info(REPO_ID).sha
    if not sha:
        raise RuntimeError(f"could not resolve a commit sha for {REPO_ID}")
    data_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(REPO_ID, repo_type="dataset", local_dir=str(data_dir), revision=sha)
    (data_dir / SHA_FILE).write_text(sha + "\n")
    return sha


def dataset_sha(data_dir: Path = DEFAULT_DATA_DIR) -> str:
    p = data_dir / SHA_FILE
    if not p.exists():
        raise FileNotFoundError(f"{p} missing: run `python -m pinned.eval download` first")
    return p.read_text().strip()


def load_problems(data_dir: Path = DEFAULT_DATA_DIR) -> list[Problem]:
    path = data_dir / "dataset.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing: run `python -m pinned.eval download` first")
    problems = [Problem.from_row(json.loads(line)) for line in path.read_text().splitlines() if line.strip()]
    ids = [p.id for p in problems]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate example_id in dataset.jsonl")
    return problems


def by_id(problems: list[Problem]) -> dict[str, Problem]:
    return {p.id: p for p in problems}
