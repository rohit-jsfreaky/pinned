"""Version facts -> HydraDB, through the batched idempotent helpers of pinned.graph only.

- Learned weights are never reset: CUES / REPLACED_BY edges that already exist keep their `w` (only new edges get
  W_DEFAULT), so re-ingesting after learning changes nothing the learner did.
- The graph holds one edge per (source, type, target). Several changes of one API at one version (e.g. signature
  AND deprecation) therefore share one CHANGED_IN edge: `kind` is the most important one (signature > deprecated >
  behaviour); the others are listed in `extra_kinds` (comma-joined) with their text in `extra`.
- Every edge of a type carries the same property set (HydraDB reads a missing property as false, upstream #186).
"""

from __future__ import annotations

from collections import defaultdict

from pinned.contract import (
    ADDED_IN, ALIAS_OF, API, CHANGED_IN, CUES, HAS_VERSION, LIBRARY, MEMBER_OF, NEXT, REMOVED_IN, REPLACED_BY, TERM,
    VERSION, W_DEFAULT,
)
from pinned.graph import (
    HydraGraph, edges_from, link_many, link_props_many, node_id, unlink, upsert_nodes, version_ord,
)

from .diff import VersionFacts, cue_paths
from .ladder import Ladder
from .model import Snapshot
from .terms import cue_terms

_CHANGE_RANK = {"signature": 0, "deprecated": 1, "behaviour": 2}
MAX_PROP = 500


def api_id(library: str, path: str) -> int:
    return node_id(API, f"{library}|{path}")


def version_id(library: str, version: str) -> int:
    return node_id(VERSION, f"{library}|{version}")


def _cut(s: str) -> str:
    return s if len(s) <= MAX_PROP else s[: MAX_PROP - 3] + "..."


def write_ladder(g: HydraGraph, ladder: Ladder, import_names: list[str]) -> dict[str, int]:
    """Library + Version nodes, HAS_VERSION and NEXT edges. NEXT edges that no longer match the ladder (a rung was
    inserted between two existing ones) are removed. Returns {version: node id}."""
    lib = ladder.library
    lib_id = upsert_nodes(g, LIBRARY, [{"key": lib, "name": lib, "import_names": ",".join(import_names)}])[0]
    existing = {r["dst"]: r.get("ingested") for r in edges_from(g, [lib_id], HAS_VERSION, dst_props=("ingested",))}
    rows = []
    for r in ladder.rungs:
        row = {"key": f"{lib}|{r.version}", "library": lib, "version": r.version, "ord": r.ord, "released": r.released,
               "pypi": r.pypi, "needed": r.needed}
        if existing.get(version_id(lib, r.version)) is None:
            row["ingested"] = False  # every Version carries it (absent reads as false anyway, #186)
        rows.append(row)
    ids = upsert_nodes(g, VERSION, rows)
    link_many(g, HAS_VERSION, [(lib_id, v) for v in ids])
    want = set(zip(ids, ids[1:]))
    for e in edges_from(g, ids, NEXT, dst_props=()):
        if (e["src"], e["dst"]) not in want:
            unlink(g, e["src"], NEXT, e["dst"])
    link_many(g, NEXT, sorted(want))
    return {r.version: i for r, i in zip(ladder.rungs, ids)}


def _existing(g: HydraGraph, rel: str, pairs: list[tuple[int, int]]) -> set[tuple[int, int]]:
    srcs = sorted({s for s, _ in pairs})
    if not srcs:
        return set()
    return {(e["src"], e["dst"]) for e in edges_from(g, srcs, rel, dst_props=())}


def write_facts(g: HydraGraph, facts: VersionFacts, cur: Snapshot, kinds: dict[str, str], docs: dict[str, str],
                ingested: bool = True) -> tuple[dict[str, int], int]:
    """Write the facts of one version. `kinds` / `docs` give kind and doc for every path the facts mention
    (from this and neighbouring snapshots). Returns (edge rows written per type, node rows written)."""
    lib = facts.library
    vid = version_id(lib, facts.version)
    counts: dict[str, int] = {}

    # ---------------- Api nodes: new ones, plus every endpoint a fact mentions (so edges never dangle)
    mention = set(facts.new_apis) | set(facts.removed) | {c.path for c in facts.changed}
    mention |= {r.path for r in facts.replaced} | {r.by for r in facts.replaced}
    mention |= set(facts.aliases) | set(facts.aliases.values()) | set(facts.members.values())
    rows = []
    for p in sorted(mention):
        row = {"key": f"{lib}|{p}", "library": lib, "path": p}
        if p in kinds:
            row["kind"] = kinds[p]
        if p in facts.new_apis and docs.get(p):
            row["doc"] = docs[p]
        rows.append(row)
    upsert_nodes(g, API, rows)
    nodes = len(rows)

    # ---------------- version events
    sig = {p: cur.apis[p].sig for p in facts.added if p in cur.apis}
    counts[ADDED_IN] = link_props_many(g, ADDED_IN, [
        (api_id(lib, p), vid, {"evidence": _cut(ev), "sig": sig.get(p, "")}) for p, ev in facts.added.items()])
    counts[REMOVED_IN] = link_props_many(g, REMOVED_IN, [
        (api_id(lib, p), vid, {"evidence": _cut(ev)}) for p, ev in facts.removed.items()])
    by_path = defaultdict(list)
    for c in facts.changed:
        by_path[c.path].append(c)
    rows = []
    for p, cs in by_path.items():
        cs = sorted(cs, key=lambda c: _CHANGE_RANK[c.kind])
        top, rest = cs[0], cs[1:]
        rows.append((api_id(lib, p), vid, {
            "kind": top.kind, "before": _cut(top.before), "after": _cut(top.after), "evidence": _cut(top.evidence),
            "extra_kinds": ",".join(c.kind for c in rest),
            "extra": _cut(" || ".join(f"{c.kind}: {c.before} -> {c.after}" for c in rest)),
        }))
    counts[CHANGED_IN] = link_props_many(g, CHANGED_IN, rows)

    # ---------------- replacements (keep learned weights of edges that already exist)
    rep = {(api_id(lib, r.path), api_id(lib, r.by)): r for r in facts.replaced}
    have = _existing(g, REPLACED_BY, list(rep))
    ord_ = version_ord(facts.version)
    counts[REPLACED_BY] = link_props_many(g, REPLACED_BY, [
        (s, d, {"w": W_DEFAULT, "evidence": _cut(r.evidence), "since_ord": ord_})
        for (s, d), r in rep.items() if (s, d) not in have])

    # ---------------- structure
    counts[ALIAS_OF] = link_props_many(g, ALIAS_OF, [
        (api_id(lib, a), api_id(lib, b), {"evidence": f"same object in {lib} {facts.version}"})
        for a, b in facts.aliases.items()])
    counts[MEMBER_OF] = link_many(g, MEMBER_OF, [(api_id(lib, p), api_id(lib, parent))
                                                 for p, parent in facts.members.items()])

    # ---------------- cue terms for new Api nodes (definitions + the shortest path of each re-export)
    cues: dict[tuple[int, int], str] = {}
    words: set[str] = set()
    cued = cue_paths(cur)
    for p in facts.new_apis:
        if p not in cued:
            continue
        for w, src in cue_terms(p, docs.get(p, ""), cur.import_names).items():
            cues[(node_id(TERM, w), api_id(lib, p))] = src
            words.add(w)
    upsert_nodes(g, TERM, [{"key": w, "word": w} for w in sorted(words)])
    nodes += len(words)
    have = _existing(g, CUES, list(cues))
    counts[CUES] = link_props_many(g, CUES, [(s, d, {"w": W_DEFAULT, "source": src})
                                             for (s, d), src in cues.items() if (s, d) not in have])

    if ingested:
        upsert_nodes(g, VERSION, [{"key": f"{lib}|{facts.version}", "ingested": True, "apis": len(cur.apis),
                                   "source_file": cur.source_file, "dynamic_python": cur.python}])
        nodes += 1
    return counts, nodes
