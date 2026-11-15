"""The train/test split: 50/50 stratified by library, seed 13, written ONCE to evidence/split.json."""

from __future__ import annotations

import json
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .dataset import Problem

SPLIT_PATH = Path("evidence/split.json")
SEED = 13


@dataclass(frozen=True)
class Split:
    seed: int
    dataset_sha: str
    train: tuple[str, ...]
    test: tuple[str, ...]

    def of(self, problem_id: str) -> str:
        if problem_id in self._train_set:
            return "train"
        if problem_id in self._test_set:
            return "test"
        raise KeyError(f"problem {problem_id} is in neither split")

    @property
    def _train_set(self) -> frozenset[str]:
        return frozenset(self.train)

    @property
    def _test_set(self) -> frozenset[str]:
        return frozenset(self.test)

    def ids(self, name: str) -> tuple[str, ...]:
        if name == "train":
            return self.train
        if name == "test":
            return self.test
        if name == "all":
            return self.train + self.test
        raise ValueError(f"unknown split {name!r} (train|test|all)")

    def to_json(self) -> dict:
        return {"seed": self.seed, "dataset_sha": self.dataset_sha, "train": list(self.train), "test": list(self.test)}


def _id_key(pid: str) -> tuple[int, str]:
    return (int(pid), pid) if pid.isdigit() else (1 << 62, pid)


def make_split(problems: list[Problem], dataset_sha: str, seed: int = SEED) -> Split:
    """Per library: sort ids, shuffle with a library-seeded RNG, first half (rounded down) -> train. Odd counts give the
    extra problem to TEST, alternating would depend on library order; this rule is order-free and deterministic."""
    groups: dict[str, list[str]] = defaultdict(list)
    for p in problems:
        groups[p.library].append(p.id)
    train: list[str] = []
    test: list[str] = []
    for lib in sorted(groups):
        ids = sorted(groups[lib], key=_id_key)
        random.Random(f"{seed}:{lib}").shuffle(ids)
        half = len(ids) // 2
        train += ids[:half]
        test += ids[half:]
    return Split(seed, dataset_sha, tuple(sorted(train, key=_id_key)), tuple(sorted(test, key=_id_key)))


def write_split_once(split: Split, path: Path = SPLIT_PATH) -> Path:
    if path.exists():
        existing = load_split(path)
        if existing.to_json() == split.to_json():
            return path
        raise FileExistsError(f"{path} already exists with different content; the split is written ONCE")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(split.to_json(), indent=1) + "\n")
    return path


def load_split(path: Path = SPLIT_PATH) -> Split:
    d = json.loads(path.read_text())
    s = Split(int(d["seed"]), str(d["dataset_sha"]), tuple(d["train"]), tuple(d["test"]))
    overlap = set(s.train) & set(s.test)
    if overlap:
        raise ValueError(f"split {path} is broken: {len(overlap)} ids in both train and test")
    return s
