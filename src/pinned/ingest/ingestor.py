"""PyPIIngestor: real library code from PyPI -> the version graph (implements contract.Ingestor)."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict
from pathlib import Path

from packaging.version import Version

from pinned.contract import API, IngestReport, VERSION
from pinned.graph import HydraGraph, node_id

from .dataset import Pair, load_pairs, read_pairs
from .deprecation import replacement_names, resolve_name, scan_module_strings
from .diff import Replacement, VersionFacts, diff
from .dynamic import VenvInspector
from .ladder import Ladder, Rung, build_ladder
from .model import Snapshot
from .pypi import PyPIClient, Release
from .snapshot import SnapshotBuilder
from .validity import validity
from .writer import write_facts, write_ladder

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CACHE = ROOT / "data" / "pypi-cache"
DEFAULT_PAIRS = ROOT / "evidence" / "ingest" / "pairs.json"
DEFAULT_DATASET = ROOT / "data" / "gitchameleon" / "dataset.jsonl"
_DOTTED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")


class PyPIIngestor:
    """`ingest_version(lib, v)` loads `v` and every not-yet-ingested ladder rung below it, diffs consecutive rungs and
    writes the facts. `fill_gap(lib, v, hint)` re-loads the module named by `hint` in full (all members, re-exports,
    dynamic inspection, removal messages) for v and its two neighbours and writes what was missing. Both are
    idempotent: re-running adds no node or edge, and never resets learned weights."""

    def __init__(self, graph: HydraGraph, cache_dir: Path | str = DEFAULT_CACHE, pairs: list[Pair] | None = None,
                 pairs_path: Path | str | None = None, dynamic: bool = True, report_dir: Path | str | None = None,
                 client: PyPIClient | None = None, inspector: VenvInspector | None = None):
        self.g = graph
        self.cache_dir = Path(cache_dir)
        self.client = client or PyPIClient(self.cache_dir)
        self.snapshots = SnapshotBuilder(self.client, self.cache_dir, dynamic=dynamic, inspector=inspector)
        self.report_dir = Path(report_dir) if report_dir else None
        self.pairs = pairs if pairs is not None else self._default_pairs(pairs_path)
        self._ladders: dict[str, Ladder] = {}
        self._written: set[str] = set()
        self._releases: dict[str, dict[str, Release]] = {}

    @staticmethod
    def _default_pairs(path: Path | str | None) -> list[Pair]:
        p = Path(path) if path else DEFAULT_PAIRS
        if p.exists():
            return read_pairs(p)
        if DEFAULT_DATASET.exists():
            return load_pairs(DEFAULT_DATASET)
        return []

    # ------------------------------------------------------------------ ladders
    def _extra_file(self) -> Path:
        return self.cache_dir / "ladder-extra.json"

    def _extras(self, library: str) -> list[str]:
        f = self._extra_file()
        return json.loads(f.read_text()).get(library, []) if f.exists() else []

    def _add_extra(self, library: str, version: str) -> None:
        f = self._extra_file()
        data = json.loads(f.read_text()) if f.exists() else {}
        data[library] = sorted(set(data.get(library, [])) | {version}, key=Version)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, indent=1))

    def release(self, library: str, pypi_version: str) -> Release:
        if library not in self._releases:
            self._releases[library] = {r.version: r for r in self.client.releases(library)}
        return self._releases[library][pypi_version]

    def ladder(self, library: str, ensure: str | None = None) -> Ladder:
        """The library's ladder (dataset pairs + extras). `ensure` adds a version that is not on it yet."""
        library = library.lower()
        lad = self._ladders.get(library)
        if ensure is not None and lad is not None:
            try:
                lad.rung(ensure)
            except KeyError:
                lad = None
                self._add_extra(library, ensure)
                self._written.discard(library)
        elif ensure is not None and lad is None:
            pairs = [p for p in self.pairs if p.library == library]
            if not any(Version(p.version) == Version(ensure) for p in pairs):
                if not any(Version(x) == Version(ensure) for x in self._extras(library)):
                    self._add_extra(library, ensure)
        if lad is None:
            self._releases.pop(library, None)
            releases = self.client.releases(library)
            self._releases[library] = {r.version: r for r in releases}
            lad = build_ladder(library, [p for p in self.pairs if p.library == library], releases,
                               self._extras(library))
            self._ladders[library] = lad
        return lad

    def write_ladder(self, library: str) -> Ladder:
        lad = self.ladder(library)
        if library not in self._written:
            first = self.snapshot(library, lad.rungs[-1])
            write_ladder(self.g, lad, first.import_names)
            self._written.add(library)
        return lad

    def snapshot(self, library: str, rung: Rung, refresh: bool = False) -> Snapshot:
        return self.snapshots.build(library, rung, self.release(library, rung.pypi), refresh=refresh)

    def _ingested(self, library: str) -> set[str]:
        rows = self.g.query(f"MATCH (v:{VERSION}) WHERE v.library = $lib AND v.ingested = true RETURN v.version AS v",
                            {"lib": library})
        return {r["v"] for r in rows}

    # ------------------------------------------------------------------ ingestion
    def ingest_version(self, library: str, version: str) -> IngestReport:
        library = library.lower()
        lad = self.ladder(library, ensure=version)
        self.write_ladder(library)
        target = lad.rung(version)
        done = self._ingested(library)
        report = None
        for rung in lad.rungs[: lad.rungs.index(target) + 1]:
            if rung is target or rung.version not in done:
                report = self._ingest_rung(lad, rung)
        assert report is not None
        return report

    def ingest_library(self, library: str) -> list[IngestReport]:
        library = library.lower()
        lad = self.write_ladder(library)
        return [self._ingest_rung(lad, r) for r in lad.rungs]

    def _facts(self, lad: Ladder, rung: Rung) -> tuple[VersionFacts, Snapshot, dict[str, str], dict[str, str]]:
        prev_rung = lad.previous(rung)
        cur = self.snapshot(lad.library, rung)
        prev = self.snapshot(lad.library, prev_rung) if prev_rung else None
        i = lad.rungs.index(rung)
        nxt = self.snapshots.cached(lad.library, lad.rungs[i + 1].pypi) if i + 1 < len(lad.rungs) else None
        later = set(nxt.apis) if nxt else set()
        facts = diff(prev, cur, rung.version, prev_rung.version if prev_rung else "", later)
        kinds, docs = {}, {}
        for s in (nxt, prev, cur):  # cur last: its kind / doc win
            if s is not None:
                kinds.update({p: a.kind for p, a in s.apis.items()})
                docs.update({p: a.doc for p, a in s.apis.items()})
        return facts, cur, kinds, docs

    def _ingest_rung(self, lad: Ladder, rung: Rung) -> IngestReport:
        t0 = time.time()
        facts, cur, kinds, docs = self._facts(lad, rung)
        t1 = time.time()
        edges, nodes = write_facts(self.g, facts, cur, kinds, docs)
        t2 = time.time()
        rep = IngestReport(lad.library, rung.version, apis=len(cur.apis), added=len(facts.added),
                           removed=len(facts.removed), changed=len({c.path for c in facts.changed}),
                           replaced=len(facts.replaced), seconds=round(t2 - t0, 3))
        self._save_report(rep, cur, edges, rung, extra={
            "nodes_written": nodes, "load_seconds": round(t1 - t0, 3), "write_seconds": round(t2 - t1, 3)})
        log.info("%s %s: %d apis, +%d -%d ~%d replaced %d (%.1fs)", lad.library, rung.version, rep.apis, rep.added,
                 rep.removed, rep.changed, rep.replaced, rep.seconds)
        return rep

    def _save_report(self, rep: IngestReport, cur: Snapshot, edges: dict[str, int], rung: Rung,
                     kind: str = "version", extra: dict | None = None) -> None:
        if self.report_dir is None:
            return
        out = self.report_dir / rep.library / (f"{rung.version}.json" if kind == "version"
                                               else f"{rung.version}.gap-{int(time.time())}.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        doc = {"report": asdict(rep), "kind": kind, "pypi": rung.pypi, "needed": rung.needed,
               "source_file": cur.source_file, "dynamic_python": cur.python,
               "dynamic_modules": len(cur.dynamic_modules), "dynamic_errors": cur.dynamic_errors[:20],
               "edges_written": edges, **(extra or {})}
        out.write_text(json.dumps(doc, indent=1) + "\n")

    # ------------------------------------------------------------------ gap-driven ingestion
    def resolve_hint(self, snap: Snapshot, hint: str) -> str:
        """A failure hint ("scipy.special.i1", "special.i1", "i1", "module 'x' has no attribute 'y'") -> one path."""
        tokens = _DOTTED.findall(hint)
        imports = snap.import_names
        attr_msg = re.search(r"module '([\w.]+)' has no attribute '(\w+)'", hint)
        if attr_msg:
            return f"{attr_msg.group(1)}.{attr_msg.group(2)}"
        rooted = [t for t in tokens if t.split(".", 1)[0] in imports]
        if rooted:
            return max(rooted, key=len)
        best = max(tokens, key=len) if tokens else hint.strip()
        suffix = [p for p in snap.apis if p.endswith("." + best)]
        if suffix:
            return min(suffix, key=lambda p: (p.count("."), p))
        return f"{imports[0]}.{best}" if imports else best

    def fill_gap(self, library: str, version: str, hint: str) -> IngestReport:
        """Re-load the module that holds `hint` in full (relaxed walk + dynamic inspection, remembered for every later
        load of this library), recompute the facts at `version` (prev -> v) and at the next rung (v -> next) and
        write them. The report counts only facts that were not there before."""
        t0 = time.time()
        library = library.lower()
        lad = self.ladder(library, ensure=version)
        if lad.rung(version).version not in self._ingested(library):
            self.ingest_version(library, version)
        rung = lad.rung(version)
        cur = self.snapshot(library, rung)
        path = self.resolve_hint(cur, hint)
        module = self._module_of(cur, path)
        i = lad.rungs.index(rung)
        around = lad.rungs[i: i + 2]  # facts AT v and AT the next rung both depend on v's snapshot
        before = {r.version: self._facts(lad, r)[0].keys() for r in around}
        self.snapshots.add_gap_modules(library, [module])  # invalidates this library's cached snapshots
        new_keys: set[tuple] = set()
        edges_total: dict[str, int] = {}
        for r in around:
            facts, snap, kinds, docs = self._facts(lad, r)
            if r is rung:
                self._removal_messages(facts, snap, path, kinds)
                cur = snap
            new_keys |= {(r.version,) + k for k in facts.keys() - before[r.version]}
            for k, v in write_facts(self.g, facts, snap, kinds, docs)[0].items():
                edges_total[k] = edges_total.get(k, 0) + v
        new_keys |= {(ver, "ADDED_IN", p) for ver, p in self._anchor_unseen(lad, rung, cur, module)}
        rels = [k[1] for k in new_keys]
        rep = IngestReport(library, rung.version, apis=len(cur.apis), added=rels.count("ADDED_IN"),
                           removed=rels.count("REMOVED_IN"), changed=rels.count("CHANGED_IN"),
                           replaced=rels.count("REPLACED_BY"), seconds=round(time.time() - t0, 3))
        self._save_report(rep, cur, edges_total, rung, kind="gap", extra={
            "hint": hint, "resolved_path": path, "module": module, "found": path in cur.apis,
            "new_facts": sorted(" ".join(map(str, k)) for k in new_keys)[:200]})
        log.info("fill_gap %s %s %r -> %s (module %s): %s", library, version, hint, path, module, asdict(rep))
        return rep

    def _anchor_unseen(self, lad: Ladder, rung: Rung, cur: Snapshot, module: str) -> list[tuple[str, str]]:
        """Paths of the gap module that exist at v but still have no ADDED_IN at or before v (they were also in the
        previous rung, so no diff of the re-checked rungs adds them): anchor them with an ADDED_IN at the oldest
        re-checked rung that has them. Older rungs are not re-checked, and the evidence says so."""
        prev_rung = lad.previous(rung)
        prev = self.snapshot(lad.library, prev_rung) if prev_rung else None
        paths = [p for p in cur.apis if p == module or p.startswith(module + ".")]
        unseen = [p for p, st in validity(self.g, lad.library, paths, rung.version).items() if st in ("missing", "added_later")]
        in_prev = [p for p in unseen if prev is not None and p in prev.apis]
        written: list[tuple[str, str]] = []
        for target, snap, group in ((prev_rung, prev, in_prev), (rung, cur, [p for p in unseen if p not in in_prev])):
            if not group or target is None or snap is None:
                continue
            facts = VersionFacts(lad.library, target.version)
            for p in group:
                facts.added[p] = (f"present in {lad.library} {target.version} ({snap.source_file}, "
                                  f"{snap.apis[p].origin}; found by gap fill of {module}; older versions not re-checked)")
                facts.new_apis.append(p)
                parent = p.rsplit(".", 1)[0]
                if parent in snap.apis:
                    facts.members[p] = parent
            kinds = {p: a.kind for p, a in snap.apis.items()}
            docs = {p: a.doc for p, a in snap.apis.items()}
            write_facts(self.g, facts, snap, kinds, docs, ingested=False)
            written += [(target.version, p) for p in facts.added]
        return written

    @staticmethod
    def _module_of(snap: Snapshot, path: str) -> str:
        """The deepest module on `path` (the path itself when it is a module). Unknown segments are kept as long as
        no class / function sits above them: a module created at runtime is still worth importing."""
        parts = path.split(".")
        module = parts[0]
        for i in range(1, len(parts) + 1):
            m = ".".join(parts[:i])
            rec = snap.apis.get(m)
            if rec is not None and rec.kind != "module":
                break
            if rec is not None or i < len(parts):
                module = m
        return module

    def _removal_messages(self, facts: VersionFacts, snap: Snapshot, path: str, kinds: dict[str, str]) -> None:
        """The hinted path does not exist at this version: look for the library's own words about it (a removal /
        deprecation message in its module source) and record them as REMOVED_IN (+ REPLACED_BY when the message
        names a replacement that exists)."""
        if path in snap.apis:
            return
        parent, _, leaf = path.rpartition(".")
        src_file = snap.module_files.get(parent)
        if not src_file or not Path(src_file).exists():
            return
        msgs = scan_module_strings(Path(src_file).read_text(encoding="utf-8", errors="replace"), leaf)
        if not msgs:
            return
        here = f"{snap.library} {facts.version}"
        facts.removed[path] = f"not in {here}; the library says: {msgs[0]}"[:500]
        kinds.setdefault(path, "attr")
        for msg in msgs:
            for name in replacement_names(msg):
                by = resolve_name(name, path, set(snap.apis), snap.import_names)
                if by:
                    facts.replaced.append(Replacement(path, by, f"{here}: {msg}"[:500]))


def api_node_id(library: str, path: str) -> int:
    return node_id(API, f"{library}|{path}")
