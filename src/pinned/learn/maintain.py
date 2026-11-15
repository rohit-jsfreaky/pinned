"""maintain(): once per iteration — path compression, entity merge, entity split, decay. All reads are batched per
library; every change is one move-log line."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from pinned.contract import (
    ADDED_IN,
    ALIAS_OF,
    API,
    CHANGED_IN,
    CUES,
    EPISODE,
    REMOVED_IN,
    REPLACED_BY,
    SHORTCUT,
    TERM,
    USED,
    VERSION,
    Graph,
    LearnReport,
)
from pinned.graph import link_props_many, set_edges_many

from .facts import load_facts
from .movelog import MoveLog
from .routes import api_id, term_id, version_id
from .weights import COMPRESS_MIN_PASSES, DECAY_MIN_FAILS, W_DECAYED, should_decay, shortcut_weight

MERGE_KINDS = ("function", "class")
TRIVIAL_SIGS = ("", "()", "(self)", "(*args, **kwargs)", "(self, *args, **kwargs)")


@dataclass
class _Use:
    """One USED edge of one episode of this run."""

    ek: str
    version: str
    ord: int
    passed: bool
    error_type: str
    error_api: str
    error: str
    aid: int
    path: str
    via: str
    cue_terms: list[str] = field(default_factory=list)


@dataclass
class MaintainState:
    """What maintain() remembers between calls in one process, so it re-reads only what can have changed.

    Merge depends only on ingested facts, which change only through ingestion, so a library is merged once per process
    and again only after a gap fill touched it (`dirty_libs`). Decay depends only on n_fail/n_pass, which change only
    in record(), so after one full scan per process only the edges record() touched are checked (`touched`).
    (HydraDB 0.4.0 runs library-filtered edge reads as full edge scans; see docs/progress/D-learn.md.)"""

    merged_libs: set[str] = field(default_factory=set)
    dirty_libs: set[str] = field(default_factory=set)
    touched: dict[str, set[tuple[int, int]]] = field(default_factory=lambda: defaultdict(set))
    full_decay_done: bool = False


class Maintainer:
    def __init__(self, g: Graph, run_id: str, log: MoveLog, state: MaintainState):
        self.g, self.run_id, self.log, self.state = g, run_id, log, state

    def run(self) -> LearnReport:
        rep = LearnReport()
        by_lib = self._episodes()
        for lib, uses in sorted(by_lib.items()):
            rep.shortcuts_added += self.compress(lib, uses)
            rep.split += self.split(lib, uses)
        need = {lib for lib in by_lib if lib not in self.state.merged_libs} | (self.state.dirty_libs & set(by_lib))
        rep.merged += self.merge(sorted(need))
        decayed = self.decay()
        rep.edges_reweighted += decayed
        rep.notes.append(
            f"libraries={len(by_lib)} shortcuts={rep.shortcuts_added} merged={rep.merged} split={rep.split} decayed={decayed}"
        )
        return rep

    # ---------------------------------------------------------------- reads
    def _episodes(self) -> dict[str, list[_Use]]:
        q = (f"MATCH (e:{EPISODE})-[u:{USED}]->(a) WHERE e.run_id = $r RETURN e.key AS ek, e.library AS lib, "
             "e.version AS version, e.ord AS ord, e.passed AS passed, e.error_type AS et, e.error_api AS eapi, "
             "e.error AS err, a.id AS aid, a.path AS path, u.via AS via, u.cue_terms AS terms")
        out: dict[str, list[_Use]] = defaultdict(list)
        for r in self.g.query(q, {"r": self.run_id}):
            if r["lib"] is None or r["ord"] is None:
                continue
            out[r["lib"]].append(_Use(
                r["ek"], r["version"], int(r["ord"]), bool(r["passed"]), r["et"] or "", r["eapi"] or "", r["err"] or "",
                r["aid"], r["path"] or "", r["via"] or "", [t for t in (r["terms"] or "").split(",") if t],
            ))
        return out

    # ---------------------------------------------------------------- path compression
    def compress(self, lib: str, uses: list[_Use]) -> int:
        """A chain Term -> Api -REPLACED_BY/ALIAS_OF-> ... -> Api that led to >= COMPRESS_MIN_PASSES passes (and more
        passes than failures) becomes SHORTCUT{w, lo_ord, hi_ord, hops} Term -> last Api, for the proven version range."""
        passes: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)   # (via, term) -> {episode: ord}
        fails: dict[str, set[str]] = defaultdict(set)                       # via -> failing episodes
        for u in uses:
            if not u.via:
                continue
            if u.passed:
                for t in u.cue_terms:
                    passes[(u.via, t)][u.ek] = u.ord
            else:
                fails[u.via].add(u.ek)
        proven = {k: v for k, v in passes.items() if len(v) >= COMPRESS_MIN_PASSES and len(v) > len(fails[k[0]])}
        if not proven:
            return 0
        want: dict[tuple[int, int], dict] = {}
        for (via, term), eps in proven.items():
            apis = via.split(">")
            src, dst = term_id(term), api_id(lib, apis[-1])
            row = {"w": shortcut_weight(len(eps)), "lo_ord": min(eps.values()), "hi_ord": max(eps.values()),
                   "hops": len(apis), "_why": via, "_eps": sorted(eps), "_term": term, "_dst": f"{lib}|{apis[-1]}"}
            prev = want.get((src, dst))
            if prev is not None:  # two chains to the same API from the same word: one edge covering both
                row = {**row, "w": max(row["w"], prev["w"]), "lo_ord": min(row["lo_ord"], prev["lo_ord"]),
                       "hi_ord": max(row["hi_ord"], prev["hi_ord"]), "hops": min(row["hops"], prev["hops"]),
                       "_eps": sorted(set(row["_eps"]) | set(prev["_eps"])), "_why": f"{prev['_why']} | {via}"}
            want[(src, dst)] = row
        have = {
            (e["src"], e["dst"]): e for e in self.g.query(
                f"MATCH (a)-[r:{SHORTCUT}]->(b) WHERE a.id IN $s AND b.id IN $d RETURN a.id AS src, b.id AS dst, "
                "r.w AS w, r.lo_ord AS lo_ord, r.hi_ord AS hi_ord, r.hops AS hops",
                {"s": list({s for s, _ in want}), "d": list({d for _, d in want})},
            )
        }
        rows: list[tuple[int, int, dict]] = []
        added = 0
        for (src, dst), row in want.items():
            props = {k: v for k, v in row.items() if not k.startswith("_")}
            old = have.get((src, dst))
            before = None
            if old is not None:
                before = {k: old[k] for k in ("w", "lo_ord", "hi_ord", "hops")}
                props = {
                    "w": max(props["w"], float(old["w"] or 0)),
                    "lo_ord": min(props["lo_ord"], int(old["lo_ord"] or props["lo_ord"])),
                    "hi_ord": max(props["hi_ord"], int(old["hi_ord"] or props["hi_ord"])),
                    "hops": int(old["hops"] or props["hops"]),
                }
                if props == before:
                    continue
            else:
                added += 1
            rows.append((src, dst, props))
            self.log.write("shortcut", {"src": row["_term"], "rel": SHORTCUT, "dst": row["_dst"]}, before, props,
                           f"chain {row['_why']} passed {len(row['_eps'])} times", row["_eps"])
        if rows:
            link_props_many(self.g, SHORTCUT, rows)
        return added

    # ---------------------------------------------------------------- entity split
    def split(self, lib: str, uses: list[_Use]) -> int:
        """One path that passes at one version and fails with a TypeError naming it at another -> CHANGED_IN at the
        upper version of every flip, so search answers per version. Skipped where the graph already has a change."""
        outcome: dict[int, dict[int, dict]] = defaultdict(dict)  # aid -> ord -> {ord, version, pass, fail, eks, err, path}
        for u in uses:
            is_type_fail = (not u.passed) and u.error_type == "TypeError" and u.error_api == u.path
            if not (u.passed or is_type_fail):
                continue
            slot = outcome[u.aid].setdefault(u.ord, {"ord": u.ord, "version": u.version, "pass": False, "fail": False, "eks": [],
                                                     "err": "", "path": u.path})
            slot["pass" if u.passed else "fail"] = True
            slot["eks"].append(u.ek)
            if is_type_fail and not slot["err"]:
                slot["err"] = u.error.strip().splitlines()[-1][:200] if u.error.strip() else "TypeError"
        flips: list[tuple[int, dict, dict]] = []
        for aid, by_ord in outcome.items():
            seq = [(o, s) for o, s in sorted(by_ord.items()) if s["pass"] != s["fail"]]  # drop ambiguous versions
            for (_, lo), (_, hi) in zip(seq, seq[1:]):
                if lo["pass"] != hi["pass"]:
                    flips.append((aid, lo, hi))
        if not flips:
            return 0
        facts = load_facts(self.g, list({a for a, _, _ in flips}))
        vids = {version_id(lib, hi["version"]) for _, _, hi in flips}
        known_versions = {r["id"] for r in self.g.query(f"MATCH (v:{VERSION}) WHERE v.id IN $ids RETURN v.id AS id",
                                                        {"ids": list(vids)})}
        rows: list[tuple[int, int, dict]] = []
        for aid, lo, hi in flips:
            f = facts[aid]
            lo_ord, hi_ord = lo["ord"], hi["ord"]
            vid = version_id(lib, hi["version"])
            if vid not in known_versions:
                self.log.write("split_skipped", {"node": f"{lib}|{lo['path']}"}, None, None,
                               f"version {lib}=={hi['version']} is not in the graph", lo["eks"] + hi["eks"])
                continue
            if any(f.has_change(lo_ord, hi_ord, k) for k in ("signature", "behaviour")):
                continue  # the graph already answers per version here
            sig_lo, sig_hi = f.signature_at(lo_ord), f.signature_at(hi_ord)
            if sig_lo and sig_hi and sig_lo != sig_hi:
                props = {"kind": "signature", "before": sig_lo, "after": sig_hi}
            else:
                props = {"kind": "behaviour", "before": _describe(lib, lo), "after": _describe(lib, hi)}
            eks = sorted(set(lo["eks"] + hi["eks"]))
            props["evidence"] = "learned from episodes: " + ",".join(eks[:6])
            rows.append((aid, vid, props))
            self.log.write("split", {"src": f"{lib}|{lo['path']}", "rel": CHANGED_IN, "dst": f"{lib}|{hi['version']}"},
                           None, props, f"passes at {lo['version'] if lo['pass'] else hi['version']}, TypeError at "
                           f"{hi['version'] if lo['pass'] else lo['version']}", eks)
        if rows:
            link_props_many(self.g, CHANGED_IN, rows)
        return len(rows)

    # ---------------------------------------------------------------- entity merge
    def merge(self, libs: list[str]) -> int:
        """Two Api nodes that are the same object -> ALIAS_OF (re-export -> definition). Same object = same library,
        same last name, same kind (function/class), identical ADDED_IN/REMOVED_IN history with identical non-trivial
        signatures, and either one path re-exports the other (its module is a parent package of the other's module) or
        both carry the same non-empty docstring line (`doc`). One read for all libraries asked."""
        if not libs:
            return 0
        where, params = ("WHERE a.library = $lib", {"lib": libs[0]}) if len(libs) == 1 else ("", {})
        wanted = set(libs)
        adds = self.g.query(
            f"MATCH (a:{API})-[r:{ADDED_IN}]->(v) {where} RETURN a.id AS id, a.library AS lib, a.path AS path, "
            "a.kind AS kind, a.doc AS doc, r.sig AS sig, v.ord AS ord", params)
        rems = self.g.query(f"MATCH (a:{API})-[r:{REMOVED_IN}]->(v) {where} RETURN a.id AS id, v.ord AS ord", params)
        info: dict[int, tuple[str, str, str, str]] = {}
        hist: dict[int, list[tuple]] = defaultdict(list)
        for r in adds:
            if r["lib"] in wanted and r["kind"] in MERGE_KINDS and r["path"] and r["ord"] is not None:
                info[r["id"]] = (r["path"], r["kind"], r["doc"] or "", r["lib"])
                hist[r["id"]].append((int(r["ord"]), "A", r["sig"] or ""))
        for r in rems:
            if r["id"] in info and r["ord"] is not None:
                hist[r["id"]].append((int(r["ord"]), "R", ""))
        groups: dict[tuple, list[int]] = defaultdict(list)
        for aid, (path, kind, _, lib) in info.items():
            h = tuple(sorted(hist[aid]))
            if all(e[2].strip() in TRIVIAL_SIGS for e in h if e[1] == "A"):
                continue
            groups[(lib, path.rsplit(".", 1)[-1], kind, h)].append(aid)
        candidates: list[tuple[int, int, str]] = []
        for ids in groups.values():
            if len(ids) < 2:
                continue
            ids.sort(key=lambda i: (info[i][0].count("."), info[i][0]))
            for i, x in enumerate(ids):
                for y in ids[i + 1:]:
                    why = _same_object(info[x][:3], info[y][:3])
                    if why:
                        candidates.append((x, y, why))
        rows: list[tuple[int, int, dict]] = []
        if candidates:
            ids = list({i for x, y, _ in candidates for i in (x, y)})
            linked: set[tuple[int, int]] = set()
            for i in range(0, len(ids), 1000):
                linked |= {(r["s"], r["d"]) for r in self.g.query(
                    f"MATCH (a)-[r:{ALIAS_OF}]->(b) WHERE a.id IN $ids RETURN a.id AS s, b.id AS d",
                    {"ids": ids[i : i + 1000]})}
            for x, y, why in candidates:
                if (x, y) in linked or (y, x) in linked:
                    continue
                props = {"evidence": f"learned: {why}"}
                rows.append((x, y, props))
                lib = info[x][3]
                self.log.write("merge", {"src": f"{lib}|{info[x][0]}", "rel": ALIAS_OF, "dst": f"{lib}|{info[y][0]}"},
                               None, props, why, [])
            if rows:
                link_props_many(self.g, ALIAS_OF, rows)
        self.state.merged_libs |= wanted
        self.state.dirty_libs -= wanted
        return len(rows)

    # ---------------------------------------------------------------- decay
    def decay(self) -> int:
        """Edges that keep leading to failures (weights.should_decay) drop to W_DECAYED < W_FLOOR. The first call in a
        process scans every reweighted edge; later calls check only the edges record() touched since."""
        total = 0
        cols = ("RETURN a.id AS src, b.id AS dst, a.key AS src_key, b.key AS dst_key, r.w AS w, "
                "r.n_pass AS n_pass, r.n_fail AS n_fail")
        for rel, label in ((CUES, TERM), (SHORTCUT, TERM), (REPLACED_BY, API)):
            if not self.state.full_decay_done:
                rows = self.g.query(f"MATCH (a:{label})-[r:{rel}]->(b) WHERE r.n_fail >= $n {cols}", {"n": DECAY_MIN_FAILS})
            else:
                pairs = self.state.touched.get(rel, set())
                rows = []
                plist = sorted(pairs)
                for i in range(0, len(plist), 1000):
                    chunk = plist[i : i + 1000]
                    got = self.g.query(f"MATCH (a)-[r:{rel}]->(b) WHERE a.id IN $s AND b.id IN $d {cols}",
                                       {"s": list({p[0] for p in chunk}), "d": list({p[1] for p in chunk})})
                    rows += [r for r in got if (r["src"], r["dst"]) in pairs]
            batch = []
            for r in rows:
                if should_decay(r["w"], r["n_pass"], r["n_fail"]):
                    batch.append((r["src"], r["dst"], {"w": W_DECAYED}))
                    self.log.write("decay", {"src": r["src_key"], "rel": rel, "dst": r["dst_key"]}, {"w": r["w"]},
                                   {"w": W_DECAYED}, f"{r['n_fail']} failures vs {r['n_pass'] or 0} passes", [])
            if batch:
                set_edges_many(self.g, rel, batch)
            total += len(batch)
        self.state.full_decay_done = True
        self.state.touched.clear()
        return total


def _describe(lib: str, slot: dict) -> str:
    if slot["pass"]:
        return f"works at {lib}=={slot['version']}"
    return f"TypeError at {lib}=={slot['version']}: {slot['err']}"[:300]


def _same_object(x: tuple[str, str, str], y: tuple[str, str, str]) -> str:
    """x is the shorter path. Returns why they are the same object, or "" if not proven."""
    (px, _, dx), (py, _, dy) = x, y
    mx, my = px.rsplit(".", 1)[0], py.rsplit(".", 1)[0]
    if mx != my and my.startswith(mx + "."):
        return f"{px} re-exports {py} (same signature at every version)"
    if dx and dx == dy:
        return f"same signature at every version and same docstring line '{dx[:80]}'"
    return ""
