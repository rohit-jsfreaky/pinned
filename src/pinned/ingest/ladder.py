"""Version ladders: which releases of a library get ingested, in order.

Ladder = every exact version the dataset needs + the latest patch of every minor series from one minor below the
oldest needed to one minor above the newest needed. A dataset version written loosely ("2", "1.13") is matched to the
PyPI release it equals under PEP 440 ("2" == "2.0.0"); its graph spelling stays the dataset's, so every part can look
the Version node up by the exact string a problem carries.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from packaging.version import Version

from pinned.graph import version_ord

from .dataset import Pair
from .pypi import PyPIError, Release


@dataclass
class Rung:
    version: str                  # graph spelling (dataset spelling for needed versions, else PyPI's)
    pypi: str                     # exact PyPI release
    ord: int
    released: str
    needed: bool                  # a GitChameleon problem asks for this version
    python: str = ""              # interpreter for dynamic inspection (dataset's for needed rungs, else nearest)
    deps: list[str] = field(default_factory=list)   # dataset `additional_dependencies` sets for needed rungs
    yanked: bool = False          # yanked on PyPI (only ever kept because the dataset pins it)


@dataclass
class Ladder:
    library: str
    rungs: list[Rung]

    def rung(self, version: str) -> Rung:
        """Find a rung by any PEP 440 spelling of its version."""
        want = Version(version)
        for r in self.rungs:
            if Version(r.version) == want:
                return r
        raise KeyError(f"{self.library} {version} is not on the ladder")

    def previous(self, rung: Rung) -> Rung | None:
        i = self.rungs.index(rung)
        return self.rungs[i - 1] if i > 0 else None

    def to_json(self) -> dict:
        return {"library": self.library, "rungs": [asdict(r) for r in self.rungs]}

    @classmethod
    def from_json(cls, d: dict) -> Ladder:
        return cls(d["library"], [Rung(**r) for r in d["rungs"]])


def _minor(v: str) -> tuple[int, int]:
    rel = Version(v).release
    return (rel[0], rel[1] if len(rel) > 1 else 0)


def build_ladder(library: str, pairs: list[Pair], releases: list[Release], extra: list[str] = ()) -> Ladder:
    """`pairs` = the dataset pairs of this library; `extra` = further exact versions asked for (CLI / fill_gap)."""
    by_version = {Version(r.version): r for r in releases}
    needed: dict[Version, Pair] = {}
    for p in pairs:
        v = Version(p.version)
        if v not in by_version:
            raise PyPIError(f"{library} {p.version} (from the dataset) is not a final release on PyPI")
        needed[v] = p
    live = [r for r in releases if not r.yanked]
    minors = sorted({_minor(r.version) for r in live} | {_minor(str(v)) for v in needed})
    chosen: dict[Version, Rung] = {}
    if needed:
        lo = minors.index(_minor(str(min(needed))))
        hi = minors.index(_minor(str(max(needed))))
        span = set(minors[max(lo - 1, 0): hi + 2])
        latest: dict[tuple[int, int], Release] = {}
        for r in live:
            if _minor(r.version) in span:
                latest[_minor(r.version)] = r  # releases are sorted, the last one wins
        for r in latest.values():
            chosen[Version(r.version)] = Rung(r.version, r.version, version_ord(r.version), r.released, False)
    for v, p in needed.items():
        r = by_version[v]
        chosen[v] = Rung(p.version, r.version, version_ord(r.version), r.released, True, p.python, p.dependency_sets,
                         r.yanked)
    for x in extra:
        v = Version(x)
        if v in chosen:
            continue
        if v not in by_version or by_version[v].yanked:
            raise PyPIError(f"{library} {x} is not a live final release on PyPI")
        r = by_version[v]
        chosen[v] = Rung(r.version, r.version, version_ord(r.version), r.released, False)
    rungs = [chosen[v] for v in sorted(chosen)]
    _assign_python(rungs)
    return Ladder(library, rungs)


def _assign_python(rungs: list[Rung]) -> None:
    """Rungs no problem asks for borrow the interpreter of the nearest needed rung (by version order)."""
    known = [r for r in rungs if r.python]
    for r in rungs:
        if not r.python and known:
            r.python = min(known, key=lambda k: abs(k.ord - r.ord)).python


def save_ladders(ladders: list[Ladder], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "rule": "every dataset version + latest patch of each minor from one minor below the oldest needed to one "
                "minor above the newest needed; final releases only",
        "libraries": len(ladders), "versions": sum(len(l.rungs) for l in ladders),
        "ladders": [l.to_json() for l in sorted(ladders, key=lambda l: l.library)],
    }
    out.write_text(json.dumps(doc, indent=1) + "\n")


def read_ladders(path: Path) -> dict[str, Ladder]:
    return {d["library"]: Ladder.from_json(d) for d in json.loads(path.read_text())["ladders"]}
