"""Append-only move log: one JSON line per learning move in `<log_dir>/<run_id>.jsonl`.

Line shape: {"ts", "run_id", "move", "keys", "before", "after", "why", "episodes"}. `keys` names the edge
({"src", "rel", "dst"} natural keys) or node ({"node"}) that moved.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

DEFAULT_DIR_ENV = "PINNED_LEARN_LOG_DIR"
DEFAULT_DIR = Path("evidence/learn")


def default_dir() -> Path:
    return Path(os.environ.get(DEFAULT_DIR_ENV) or DEFAULT_DIR)


def _safe(run_id: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", run_id).strip("._")
    if not name:
        raise ValueError(f"run_id {run_id!r} gives an empty log file name")
    return name


class MoveLog:
    def __init__(self, run_id: str, log_dir: Path | str | None = None):
        self.run_id = run_id
        self.path = Path(log_dir or default_dir()) / f"{_safe(run_id)}.jsonl"
        self._lock = threading.Lock()
        self.count = 0

    def write(self, move: str, keys: dict[str, Any], before: Any, after: Any, why: str, episodes: list[str]) -> None:
        line = {
            "ts": round(time.time(), 3), "run_id": self.run_id, "move": move, "keys": keys,
            "before": before, "after": after, "why": why, "episodes": episodes,
        }
        data = json.dumps(line, sort_keys=True, default=str)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(data + "\n")
            self.count += 1

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]
