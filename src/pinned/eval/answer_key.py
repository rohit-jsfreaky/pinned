"""The GitChameleon ANSWER KEY. Only two uses are allowed (PLAN.md section 8):
- `hidden_test_path`: the runner executes a candidate against the hidden test (the source never leaves the runner);
- `ground_truth`: the runner self-check (`selfcheck.py`) proves our venvs + runner are right.
Nothing from here may enter a prompt, a Context, an Episode's text fields beyond the scrubbed error_text, or the graph.
"""

from __future__ import annotations

import json
from pathlib import Path

from .dataset import DEFAULT_DATA_DIR


def hidden_test_path(problem_id: str, data_dir: Path = DEFAULT_DATA_DIR) -> Path:
    p = data_dir / "hidden_tests" / f"test_sample_{problem_id}.py"
    if not p.exists():
        raise FileNotFoundError(f"hidden test missing: {p}")
    return p


def ground_truth(data_dir: Path = DEFAULT_DATA_DIR) -> dict[str, str]:
    """example_id -> ground-truth solution code. Runner self-check ONLY."""
    path = data_dir / "ground_truth_solutions.jsonl"
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            out[str(row["example_id"])] = str(row["answer"])
    return out
