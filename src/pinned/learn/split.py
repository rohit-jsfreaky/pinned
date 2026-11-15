"""The sacred train/test split: learning refuses any episode of a held-out (test) problem."""

from __future__ import annotations

import json
from pathlib import Path


class TestSplitError(ValueError):
    """Raised when an episode from the held-out test split reaches the learner."""

    __test__ = False  # not a pytest test class


class SplitGuard:
    """`split_path=None` = live product use: nothing is refused. Otherwise reads {"seed", "train", "test"}."""

    def __init__(self, split_path: str | Path | None):
        self.path = Path(split_path) if split_path is not None else None
        self.test: frozenset[str] = frozenset()
        if self.path is not None:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                raise FileNotFoundError(f"split file not found: {self.path}") from None
            except json.JSONDecodeError as e:
                raise ValueError(f"split file {self.path} is not valid JSON: {e}") from None
            if not isinstance(data, dict) or not isinstance(data.get("test"), list):
                raise ValueError(f"split file {self.path} has no 'test' list")
            self.test = frozenset(str(x) for x in data["test"])

    def check(self, problem_id: str) -> None:
        if str(problem_id) in self.test:
            raise TestSplitError(
                f"problem {problem_id!r} is in the held-out test split ({self.path}); learning from it is not allowed"
            )
