"""GitChameleon 2.0 problem file -> the (library, version) pairs to ingest.

Only `library`, `version`, `python_version` and `additional_dependencies` are read here. The answer key
(`solution`, ground-truth solutions, hidden tests) is never downloaded or read by ingestion.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download
from packaging.version import Version

REPO = "cabbage972/GitChameleon-2.0"
FILE = "dataset.jsonl"
PAIR_FIELDS = ("library", "version", "python_version", "additional_dependencies")


@dataclass
class Pair:
    library: str
    version: str                                    # exactly as the dataset writes it ("2", "1.13", "2.0.0")
    problems: int = 0
    python_versions: list[str] = field(default_factory=list)
    dependency_sets: list[str] = field(default_factory=list)   # distinct `additional_dependencies` strings

    @property
    def python(self) -> str:
        """The interpreter most problems of this pair use (ties -> newest)."""
        return self.python_versions[0] if self.python_versions else ""


def download(data_dir: Path) -> tuple[Path, str]:
    """Fetch ONLY dataset.jsonl at a pinned commit. Returns (path, dataset commit sha)."""
    sha = HfApi().dataset_info(REPO).sha
    path = hf_hub_download(REPO, FILE, repo_type="dataset", revision=sha, local_dir=str(data_dir / "gitchameleon"))
    return Path(path), sha


def read_rows(path: Path, fields: tuple[str, ...] = PAIR_FIELDS) -> list[dict]:
    """Rows reduced to `fields` (the caller says which fields it may see)."""
    out = []
    with path.open() as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                out.append({k: row.get(k) for k in fields})
    return out


def pairs_from_rows(rows: list[dict]) -> list[Pair]:
    py: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    deps: dict[tuple[str, str], set[str]] = defaultdict(set)
    count: dict[tuple[str, str], int] = defaultdict(int)
    for r in rows:
        key = (str(r["library"]).strip().lower(), str(r["version"]).strip())
        count[key] += 1
        py[key][str(r.get("python_version") or "").strip()] += 1
        deps[key].add(" ".join(str(r.get("additional_dependencies") or "").split()))
    pairs = []
    for key in sorted(count, key=lambda k: (k[0], Version(k[1]))):
        pys = sorted(py[key], key=lambda p: (-py[key][p], [-int(x) for x in p.split(".") if x.isdigit()]))
        pairs.append(Pair(key[0], key[1], count[key], [p for p in pys if p], sorted(deps[key])))
    return pairs


def load_pairs(path: Path) -> list[Pair]:
    return pairs_from_rows(read_rows(path))


def save_pairs(pairs: list[Pair], out: Path, dataset_sha: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "dataset": REPO, "file": FILE, "commit": dataset_sha, "fields_used": list(PAIR_FIELDS),
        "pairs": len(pairs), "libraries": len({p.library for p in pairs}), "problems": sum(p.problems for p in pairs),
        "items": [p.__dict__ for p in pairs],
    }
    out.write_text(json.dumps(doc, indent=1) + "\n")


def read_pairs(path: Path) -> list[Pair]:
    return [Pair(**d) for d in json.loads(path.read_text())["items"]]
