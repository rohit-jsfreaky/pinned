"""Two consecutive snapshots of a library -> the version facts to write (pure; no graph, no network).

prev = None means `cur` is the first rung of the ladder: everything in it is ADDED_IN there.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .deprecation import replacement_names, resolve_name
from .model import ApiRecord, Snapshot

MAX_MOVE_CANDIDATES = 5


@dataclass(frozen=True)
class Change:
    path: str
    kind: str          # signature | deprecated | behaviour
    before: str
    after: str
    evidence: str


@dataclass(frozen=True)
class Replacement:
    path: str
    by: str
    evidence: str


@dataclass
class VersionFacts:
    library: str
    version: str                                   # graph spelling of the version these facts happen at
    added: dict[str, str] = field(default_factory=dict)        # path -> evidence
    removed: dict[str, str] = field(default_factory=dict)      # path -> evidence
    changed: list[Change] = field(default_factory=list)
    replaced: list[Replacement] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)      # re-export path -> canonical public path
    members: dict[str, str] = field(default_factory=dict)      # path -> parent path
    new_apis: list[str] = field(default_factory=list)          # paths whose Api node + cues must be written

    def keys(self) -> set[tuple]:
        """Every fact as a hashable key (used to count what a re-run or a gap fill really added)."""
        out: set[tuple] = {("ADDED_IN", p) for p in self.added} | {("REMOVED_IN", p) for p in self.removed}
        out |= {("CHANGED_IN", c.path, c.kind, c.after) for c in self.changed}
        out |= {("REPLACED_BY", r.path, r.by) for r in self.replaced}
        out |= {("ALIAS_OF", a, b) for a, b in self.aliases.items()}
        return out


def _object_deprecations(rec: ApiRecord) -> list[str]:
    return [d.message for d in rec.deprecations if d.scope == "object"]


def _partial_deprecations(rec: ApiRecord) -> list[str]:
    return [d.message for d in rec.deprecations if d.scope != "object"]


def canonical_paths(snap: Snapshot) -> dict[str, str]:
    """Re-export path -> canonical public path of the same object (its definition when that is public, else the
    shortest public path that shares the definition)."""
    by_target: dict[str, list[str]] = {}
    for path, rec in snap.apis.items():
        by_target.setdefault(rec.target or path, []).append(path)
    out: dict[str, str] = {}
    for target, paths in by_target.items():
        canon = target if target in snap.apis else min(paths, key=lambda p: (p.count("."), len(p), p))
        for p in paths:
            if p != canon:
                out[p] = canon
    return out


def cue_paths(snap: Snapshot) -> set[str]:
    """Paths that get CUES edges: every definition, plus the shortest public path of each re-exported object.
    Inherited copies (`sympy.Symbol.subs` for each of ~1,000 subclasses) and longer re-exports reach their words
    through ALIAS_OF instead, so a word does not fan out to a thousand copies of one method."""
    out: set[str] = set()
    shortest: dict[str, str] = {}
    for path, rec in snap.apis.items():
        if not rec.target:
            out.add(path)
            continue
        cur = shortest.get(rec.target)
        if cur is None or (path.count("."), len(path), path) < (cur.count("."), len(cur), cur):
            shortest[rec.target] = path
    return out | set(shortest.values())


def diff(prev: Snapshot | None, cur: Snapshot, version: str, prev_version: str = "",
         later_paths: set[str] | None = None) -> VersionFacts:
    """Facts that happen at `version` (graph spelling of cur). `later_paths` = paths of newer snapshots, used only
    to resolve "use X instead" names that do not exist yet in `cur`."""
    lib = cur.library
    facts = VersionFacts(lib, version)
    here = f"{lib} {version}"
    p_apis = prev.apis if prev else {}
    known = set(cur.apis) | (later_paths or set())

    for path, rec in cur.apis.items():
        old = p_apis.get(path)
        if old is None:
            facts.added[path] = (f"present in {here} ({cur.source_file}, {rec.origin})"
                                 + (f"; absent in {prev_version}" if prev else "; first ingested version"))
            facts.new_apis.append(path)
            parent = path.rsplit(".", 1)[0] if "." in path else ""
            if parent in cur.apis:
                facts.members[path] = parent
            continue
        if old.sig and rec.sig and old.sig != rec.sig:
            facts.changed.append(Change(path, "signature", old.sig, rec.sig, f"signature differs: {prev_version} -> {version}"))
        if old.kind != rec.kind and {old.kind, rec.kind} != {"function", "method"}:
            facts.changed.append(Change(path, "behaviour", f"kind {old.kind}", f"kind {rec.kind}",
                                        f"object kind differs: {prev_version} -> {version}"))

    for path in p_apis:
        if path not in cur.apis:
            facts.removed[path] = f"absent in {here} ({cur.source_file}); present in {prev_version}"

    # deprecation evidence that appears (or changes) at this version
    for path, rec in cur.apis.items():
        old = p_apis.get(path)
        now = _object_deprecations(rec)
        before = _object_deprecations(old) if old else []
        if now and set(now) != set(before):
            facts.changed.append(Change(path, "deprecated", " | ".join(before)[:500], " | ".join(now)[:500],
                                        f"deprecation evidence in {here} ({rec.deprecations[0].source})"))
            for msg in now:
                for name in replacement_names(msg):
                    by = resolve_name(name, path, known, cur.import_names)
                    if by:
                        facts.replaced.append(Replacement(path, by, f"{here}: {msg}"[:500]))
        elif before and not now and old is not None:
            facts.changed.append(Change(path, "behaviour", "deprecated: " + " | ".join(before)[:400],
                                        "no longer deprecated", f"no deprecation evidence in {here}"))
        part_now = _partial_deprecations(rec)
        part_before = _partial_deprecations(old) if old else []
        if part_now and set(part_now) != set(part_before):
            facts.changed.append(Change(path, "behaviour", " | ".join(part_before)[:500],
                                        "partial deprecation (some arguments / branches): " + " | ".join(part_now)[:450],
                                        f"conditional deprecation warning in {here}"))

    facts.replaced += _moves(prev, cur, facts, here)
    facts.replaced = list({(r.path, r.by): r for r in facts.replaced}.values())

    canon = canonical_paths(cur)
    fresh = set(facts.new_apis)
    for path, c in canon.items():
        if path in fresh or c in fresh:
            facts.aliases[path] = c
    return facts


def _moves(prev: Snapshot | None, cur: Snapshot, facts: VersionFacts, here: str) -> list[Replacement]:
    """Same object, new place: a removed module-level function/class whose name (and kind) shows up elsewhere in
    `cur`; members of a moved class follow their class."""
    if prev is None or not facts.removed:
        return []
    by_leaf: dict[str, list[str]] = {}
    for path, rec in cur.apis.items():
        parent = path.rsplit(".", 1)[0]
        if rec.kind in ("function", "class") and parent in cur.apis and cur.apis[parent].kind == "module":
            by_leaf.setdefault(path.rsplit(".", 1)[1], []).append(path)
    moved: dict[str, str] = {}
    out: list[Replacement] = []
    for path in sorted(facts.removed, key=lambda p: p.count(".")):
        old = prev.apis[path]
        parent, _, leaf = path.rpartition(".")
        if parent in moved:  # member of a moved class
            dst = f"{moved[parent]}.{leaf}"
            if dst in cur.apis:
                moved[path] = dst
                out.append(Replacement(path, dst, f"{here}: moved with its class {parent} -> {moved[parent]}"))
            continue
        if old.kind not in ("function", "class") or prev.apis.get(parent, ApiRecord(parent, "")).kind != "module":
            continue
        cands = [c for c in by_leaf.get(leaf, []) if cur.apis[c].kind == old.kind]
        if not cands or len(cands) > MAX_MOVE_CANDIDATES:
            continue
        old_target = old.target or path

        def rank(c: str) -> tuple:
            rec = cur.apis[c]
            # same object first, then a definition over a re-export (ALIAS_OF links the two), new, same signature
            return ((rec.target or c) != old_target, bool(rec.target), c not in facts.added, rec.sig != old.sig,
                    c.count("."), c)

        best = min(cands, key=rank)
        moved[path] = best
        out.append(Replacement(path, best, f"{here}: {path} removed while {best} ({old.kind}, same name) is present"))
    return out
