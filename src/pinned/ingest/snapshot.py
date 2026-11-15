"""One release -> its public API snapshot: static griffe load, then dynamic inspection for compiled modules.

Snapshots are cached as JSON under <cache>/snapshots/<library>/<version>.json (keyed by SNAPSHOT_FORMAT, so a change
to the loading rules re-loads instead of silently mixing old and new snapshots).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from packaging.utils import canonicalize_name

from .dynamic import DynamicError, VenvInspector
from .ladder import Rung
from .model import ApiRecord, Snapshot
from .pypi import PyPIClient, Release, public_import_names
from .static import SKIP_MODULES, StaticResult, load_static

log = logging.getLogger(__name__)

SNAPSHOT_FORMAT = 9


@dataclass
class Plan:
    """What dynamic inspection must cover for one release."""

    modules: list[str]


def compiled_plan(static: StaticResult, compiled: list[str]) -> Plan:
    """Public modules static loading cannot fully see.

    A public module is planned when it imports from a compiled extension module (directly or by wildcard), when one
    of its re-exports could not be resolved statically, when its `__all__` is computed at runtime, or when it is
    itself a compiled module with a public name.
    """
    comp = set(compiled)

    def is_compiled(target: str) -> bool:
        parts = target.split(".")
        return any(".".join(parts[:i]) in comp for i in range(1, len(parts) + 1))

    planned: set[str] = set()
    for mod, targets in static.imports.items():
        if any(is_compiled(t) for t in targets):
            planned.add(mod)
    for path, target in static.unresolved.items():
        # a re-export griffe could not follow (compiled target, runtime-built name, stub-only chain): import it
        parent = path.rsplit(".", 1)[0]
        if parent in static.apis and static.apis[parent].kind == "module":
            planned.add(parent)
    planned |= static.computed_all  # `__all__` only exists at runtime
    for name in comp:
        if not any(p.startswith("_") for p in name.split(".")) and name.split(".", 1)[0] in {
                m.split(".", 1)[0] for m in static.apis}:
            planned.add(name)
    return Plan(sorted(planned))


def merge(static: dict[str, ApiRecord], dynamic: list[ApiRecord], compiled: list[str]) -> int:
    """Fold dynamic records into the static ones. Static wins where it saw the object; dynamic fills what static
    could not see: missing paths, and objects static only knew as an attribute / an unresolved alias into a
    compiled module (scipy.special.i1 from a .pyi stub). Returns how many paths dynamic added or improved."""
    comp = set(compiled)
    changed = 0
    for rec in dynamic:
        cur = static.get(rec.path)
        if cur is None:
            static[rec.path] = rec
            changed += 1
            continue
        into_compiled = any(cur.target.startswith(c + ".") or cur.target == c for c in comp) if cur.target else False
        if cur.kind == "attr" and rec.kind in ("function", "class", "method") and (into_compiled or not cur.sig):
            cur.kind, cur.sig, cur.origin = rec.kind, rec.sig, "dynamic"
            cur.doc = cur.doc or rec.doc
            changed += 1
        elif not cur.sig and rec.sig and cur.kind == rec.kind and cur.kind != "attr":
            cur.sig = rec.sig
            changed += 1
    return changed


class SnapshotBuilder:
    def __init__(self, client: PyPIClient, cache_dir: Path, dynamic: bool = True, inspector: VenvInspector | None = None):
        self.client = client
        self.cache_dir = Path(cache_dir)
        self.dynamic = dynamic
        self.inspector = inspector or VenvInspector(self.cache_dir / "venvs")

    def path(self, library: str, version: str) -> Path:
        return self.cache_dir / "snapshots" / canonicalize_name(library) / f"{version}.json"

    # ------------------------------------------------------------------ gap modules (fill_gap), per library
    def _gap_file(self) -> Path:
        return self.cache_dir / "gap-modules.json"

    def gap_modules(self, library: str) -> list[str]:
        f = self._gap_file()
        return sorted(json.loads(f.read_text()).get(library, [])) if f.exists() else []

    def add_gap_modules(self, library: str, modules: list[str]) -> bool:
        """Remember modules to walk in full for every version of `library`. True when the set grew."""
        f = self._gap_file()
        data = json.loads(f.read_text()) if f.exists() else {}
        cur = set(data.get(library, []))
        if set(modules) <= cur:
            return False
        data[library] = sorted(cur | set(modules))
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, indent=1))
        return True

    # ------------------------------------------------------------------ snapshots
    def cached(self, library: str, version: str) -> Snapshot | None:
        """A cached snapshot, only if it was built by the same rules and the same gap modules as now (so a diff never
        compares a deep snapshot with a shallow one)."""
        p = self.path(library, version)
        if not p.exists():
            return None
        d = json.loads(p.read_text())
        if d.get("format") != SNAPSHOT_FORMAT or d.get("gap_modules", []) != self.gap_modules(library) \
                or d.get("dynamic", True) != self.dynamic:
            return None
        return Snapshot.from_json(d)

    def build(self, library: str, rung: Rung, release: Release, refresh: bool = False) -> Snapshot:
        """The snapshot of `rung` (cached). Gap modules of the library are walked in full and inspected dynamically."""
        if not refresh:
            snap = self.cached(library, rung.pypi)
            if snap is not None:
                return snap
        gaps = self.gap_modules(library)
        ex = self.client.fetch_sources(library, release, rung.python)
        names = public_import_names(ex.top_level)
        loadable = [t for t in ex.top_level if t not in SKIP_MODULES]  # wheels that ship a stray `test/` package
        static = load_static(ex.root, loadable, names, relaxed=set(gaps))
        snap = Snapshot(library, rung.pypi, names, ex.file, apis=static.apis)
        plan = compiled_plan(static, ex.compiled)
        modules = sorted(set(plan.modules) | set(gaps))
        if self.dynamic and modules:
            try:
                res = self.inspector.inspect(library, rung.pypi, release.released, rung.python, release.files,
                                             rung.deps, modules, ex.top_level)
                merge(snap.apis, res.records, ex.compiled)
                snap.python = res.python
                snap.dynamic_modules = sorted(set(modules) - set(res.errors))
                snap.dynamic_errors = [f"{m}: {e}" for m, e in sorted(res.errors.items())]
                snap.dynamic_errors += [f"{m}: note: {e}" for m, e in sorted(res.notes.items())]
            except DynamicError as e:
                snap.dynamic_errors = [str(e)[:2000]]
                log.warning("%s", e)
        snap.dynamic_errors += [f"static: {e}" for e in static.errors]
        snap.module_files = {m: str(f) for m, f in static.modules.items()}
        self.save(snap, gaps)
        return snap

    def save(self, snap: Snapshot, gaps: list[str]) -> None:
        p = self.path(snap.library, snap.version)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"format": SNAPSHOT_FORMAT, "gap_modules": gaps, "dynamic": self.dynamic,
                                 **snap.to_json()}))
