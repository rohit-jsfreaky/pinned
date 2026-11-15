"""PlainStore — the ablation arm. The SAME facts and the SAME learned signals as the graph, kept in plain Python
dicts, with NO traversal: no replacement chains, no aliases, no multi-hop, no shortcuts by compression.

Per library it holds: which paths exist at which versions (ADDED_IN / REMOVED_IN / CHANGED_IN events, so the exact
contract validity rule applies), signatures, word -> path weights (CUES, with SHORTCUT weights folded in as plain
word -> path weights), their pass/fail counts, and episode outcomes. It answers `Search` and learns as a `Learner`
from dicts only; it never keeps a graph handle. Built from a graph export so both arms start from identical data.
This is how we answer "would it fall apart on a plain store?".
"""

from __future__ import annotations

import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pinned.contract import (
    ADDED_IN,
    API,
    CHANGED_IN,
    CUES,
    EPISODE,
    REMOVED_IN,
    SHORTCUT,
    USED,
    VERSION,
    W_FLOOR,
    ApiTimeline,
    Context,
    ContextItem,
    Episode,
    Finding,
    Graph,
    LearnReport,
    VersionFact,
)
from pinned.graph import node, version_ord

from . import text
from .code import analyse
from .facts import ApiFacts, has_param, usable
from .routes import library_id, used_items
from .split import SplitGuard
from .weights import W_DECAYED, W_LEARNED, bump, should_decay

CODE_BONUS = 1.0
MAX_TERMS = 48
MAX_LEARN_TERMS = 24
FORMAT = "pinned-plainstore/1"

_NOTES = {
    "valid": "valid at this version",
    "deprecated": "deprecated at this version (still works)",
    "removed": "removed before this version",
    "added_later": "only added in a later version",
    "missing": "not in this library",
}


@dataclass
class LibData:
    import_names: list[str] = field(default_factory=list)
    versions: dict[str, int] = field(default_factory=dict)                 # version -> ord
    events: dict[str, list[list]] = field(default_factory=dict)            # path -> [[ord, ADDED_IN|REMOVED_IN, sig]]
    changes: dict[str, list[list]] = field(default_factory=dict)           # path -> [[ord, kind, after]]
    kinds: dict[str, str] = field(default_factory=dict)                    # path -> kind
    cues: dict[str, dict[str, float]] = field(default_factory=dict)        # word -> path -> w
    stats: dict[str, dict[str, list[int]]] = field(default_factory=dict)   # word -> path -> [n_pass, n_fail]

    def facts(self, path: str) -> ApiFacts:
        return ApiFacts([tuple(e) for e in self.events.get(path, [])],
                        [tuple(c) for c in self.changes.get(path, [])]).sort()


class PlainStore:
    """Implements BOTH `Search` and `Learner` over plain dicts. `split_path` guards learning like GraphLearner."""

    def __init__(self, libs: dict[str, LibData] | None = None, episodes: list[dict] | None = None,
                 split_path: str | Path | None = None):
        self.libs: dict[str, LibData] = libs or {}
        self.episodes: list[dict] = episodes or []
        self.guard = SplitGuard(split_path)
        self._seen = {e["key"] for e in self.episodes}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ build from the graph (identical data)
    @classmethod
    def from_graph(cls, graph: Graph, library: str | Iterable[str], split_path: str | Path | None = None) -> PlainStore:
        names = [library] if isinstance(library, str) else list(dict.fromkeys(library))
        names = [n.strip().lower() for n in names]
        store = cls(_export(graph, names), _export_episodes(graph, names), split_path=split_path)
        return store

    def refresh_facts(self, graph: Graph) -> None:
        """Re-read the FACTS (after gap-driven ingestion grew the graph) but keep this store's own learned weights:
        word -> path pairs it already holds keep their weight; new ingested cues come in at their graph weight."""
        with self._lock:
            fresh_all = _export(graph, list(self.libs))
            for lib, old in self.libs.items():
                fresh = fresh_all[lib]
                for word, row in fresh.cues.items():
                    mine = old.cues.setdefault(word, {})
                    for path, w in row.items():
                        mine.setdefault(path, w)
                old.import_names, old.versions = fresh.import_names, fresh.versions
                old.events, old.changes, old.kinds = fresh.events, fresh.changes, fresh.kinds

    # ------------------------------------------------------------------ persistence
    def to_dict(self) -> dict[str, Any]:
        return {"format": FORMAT, "libs": {k: v.__dict__ for k, v in self.libs.items()}, "episodes": self.episodes}

    @classmethod
    def from_dict(cls, data: dict[str, Any], split_path: str | Path | None = None) -> PlainStore:
        if data.get("format") != FORMAT:
            raise ValueError(f"not a PlainStore export (format={data.get('format')!r})")
        return cls({k: LibData(**v) for k, v in data["libs"].items()}, list(data.get("episodes", [])), split_path)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), sort_keys=True), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path, split_path: str | Path | None = None) -> PlainStore:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")), split_path)

    # ------------------------------------------------------------------ small lookups
    def _lib(self, library: str) -> LibData | None:
        return self.libs.get(library.strip().lower())

    def valid_paths(self, library: str, version: str) -> list[str]:
        """Every path usable at that version (also the universe for ShuffledSearch's random items)."""
        data = self._lib(library)
        if data is None:
            return []
        o = version_ord(version)
        return sorted(p for p in data.events if usable(data.facts(p).status_at(o)))

    def _roots(self, data: LibData, library: str) -> set[str]:
        return set(data.import_names) or {library.replace("-", "_")}

    # ------------------------------------------------------------------ Search
    def context_for(self, problem: str, starting_code: str, library: str, version: str, k: int = 8) -> Context:
        ctx = Context(library=library, version=version)
        data = self._lib(library)
        try:
            o = version_ord(version)
        except ValueError:
            return ctx
        if data is None:
            return ctx
        scores: dict[str, float] = defaultdict(float)
        with self._lock:
            for word in text.terms(problem, limit=MAX_TERMS):
                row = {p: w for p, w in data.cues.get(word, {}).items() if w >= W_FLOOR}
                if not row:
                    continue
                idf = 1.0 / math.log2(2 + len(row))
                for p, w in row.items():
                    scores[p] += w * idf
        use = analyse(starting_code, self._roots(data, library))
        for p in use.paths - use.modules:
            if p in data.events:
                scores[p] += CODE_BONUS
        ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))[: max(0, k)]
        for path, score in ranked:
            f = data.facts(path)
            status = f.status_at(o)
            ctx.items.append(ContextItem(library=library, path=path, status=status, signature=f.signature_at(o),
                                         note=_NOTES.get(status, ""), score=round(score, 4)))
        return ctx

    def check_calls(self, code: str, library: str, version: str) -> list[Finding]:
        data = self._lib(library)
        if data is None:
            return [Finding(call=library, status="unknown", fix=f"library {library!r} is not in this store")]
        use = analyse(code, self._roots(data, library))
        if not use.parsed:
            return [Finding(call="<code>", status="unknown", fix=f"code does not parse: {use.error}")]
        o = version_ord(version)
        out: list[Finding] = []
        called = set()
        for c in use.calls:
            called.add(c.path)
            f = data.facts(c.path)
            status = f.status_at(o)
            fix = ""
            sig = f.signature_at(o)
            if usable(status) and sig:
                bad = [kw for kw in c.keywords if not has_param(sig, kw) and "**" not in sig]
                if bad:
                    status, fix = "changed", f"no parameter {', '.join(bad)} in {c.path}{sig}"
            out.append(Finding(call=c.path, status=status, fix=fix, line=c.line))
        for p in sorted(use.paths - use.modules - called):  # imported names / attributes used without a call
            out.append(Finding(call=p, status=data.facts(p).status_at(o)))
        return out

    def explain_api(self, library: str, path: str) -> ApiTimeline:
        data = self._lib(library)
        tl = ApiTimeline(library=library, path=path)
        if data is None:
            return tl
        tl.kind = data.kinds.get(path, "")
        f = data.facts(path)
        for version, o in sorted(data.versions.items(), key=lambda x: x[1]):
            status = f.status_at(o)
            tl.versions.append(VersionFact(version, o, status, f.signature_at(o), _NOTES.get(status, "")))
        return tl

    # ------------------------------------------------------------------ Learner
    def record(self, episode: Episode) -> LearnReport:
        self.guard.check(episode.problem_id)
        rep = LearnReport()
        lib = episode.library.strip().lower()
        key = f"{episode.run_id}|{episode.problem_id}|{episode.iteration}"
        with self._lock:
            if key in self._seen:
                rep.notes.append(f"episode {key} already recorded; nothing changed")
                return rep
            data = self.libs.setdefault(lib, LibData())
            ctx_roots = {it.path.split(".", 1)[0] for it in (episode.context.items if episode.context else [])}
            use = analyse(episode.code, self._roots(data, lib) | ctx_roots)
            items = used_items(episode.context, use)
            code_paths = {p for p in use.paths - use.modules if p in data.events}
            item_paths = {it.path for it in items if it.path in data.events}
            targets = item_paths if episode.context is not None else code_paths
            words = text.terms(episode.problem, limit=MAX_TERMS)
            touched: set[tuple[str, str]] = set()
            for word in words:
                for path in targets:
                    if path in data.cues.get(word, {}):
                        self._move(data, word, path, episode.passed)
                        touched.add((word, path))
                        rep.edges_reweighted += 1
            if episode.passed:
                learned = 0
                for word in words[:MAX_LEARN_TERMS]:
                    for path in code_paths | item_paths:
                        if (word, path) in touched:
                            continue
                        if path in data.cues.get(word, {}):
                            self._move(data, word, path, True)
                            rep.edges_reweighted += 1
                        else:
                            data.cues.setdefault(word, {})[path] = W_LEARNED
                            data.stats.setdefault(word, {})[path] = [1, 0]
                            learned += 1
                if learned:
                    rep.notes.append(f"learned cues added: {learned}")
            self.episodes.append({"key": key, "library": lib, "version": episode.version, "passed": bool(episode.passed),
                                  "error_type": episode.error_type or "", "used": sorted(code_paths | item_paths)})
            self._seen.add(key)
        return rep

    @staticmethod
    def _move(data: LibData, word: str, path: str, passed: bool) -> None:
        data.cues[word][path] = bump(data.cues[word][path], passed)
        st = data.stats.setdefault(word, {}).setdefault(path, [0, 0])
        st[0 if passed else 1] += 1

    def maintain(self) -> LearnReport:
        """Only decay: path compression, merge and split need traversal, which a plain store does not have."""
        rep = LearnReport()
        with self._lock:
            for data in self.libs.values():
                for word, row in data.stats.items():
                    for path, (n_pass, n_fail) in row.items():
                        w = data.cues.get(word, {}).get(path)
                        if w is not None and should_decay(w, n_pass, n_fail):
                            data.cues[word][path] = W_DECAYED
                            rep.edges_reweighted += 1
        rep.notes.append(f"decayed={rep.edges_reweighted} (plain store: no compression, merge or split)")
        return rep


# ---------------------------------------------------------------------- graph export (read once, then dicts only)
def _scope(libs: list[str], alias: str) -> tuple[str, dict]:
    """One library: filter on the server. Several: one unfiltered read, filtered here (HydraDB 0.4.0 runs a
    library-filtered edge read as a full scan of that edge type anyway, so one scan beats one per library)."""
    return (f"WHERE {alias}.library = $lib", {"lib": libs[0]}) if len(libs) == 1 else ("", {})


EXPORT_THREADS = 8


def _export(g: Graph, libs: list[str]) -> dict[str, LibData]:
    """Read the facts of `libs` with ANCHORED reads only. On the full graph (~1M Api, ~4.6M edges) HydraDB 0.4.0
    rejects an unanchored `MATCH (a:Api)-[r]->(v)` (label-candidate admission cap 250k) and a library-filtered edge
    read is a full edge scan that hits the 30 s query timeout. Edges read INTO a known Version id, or INTO a chunk of
    known Api ids, use the id index and return in milliseconds, so: versions -> ADDED_IN / REMOVED_IN / CHANGED_IN
    per version -> CUES / SHORTCUT per chunk of Api ids. Same rows as before, just reached by id."""
    out = {lib: LibData() for lib in libs}
    for lib, data in out.items():
        row = node(g, "Library", library_id(lib), ("import_names",))
        data.import_names = [x.strip() for x in str((row or {}).get("import_names") or "").split(",") if x.strip()]
    vids: list[int] = []
    for r in g.query(f"MATCH (v:{VERSION}) RETURN v.id AS id, v.library AS lib, v.version AS version, v.ord AS ord"):
        if r["lib"] in out and r["version"] is not None and r["ord"] is not None:
            out[r["lib"]].versions[r["version"]] = int(r["ord"])
            vids.append(int(r["id"]))
    rel_cols = {ADDED_IN: "a.kind AS kind, r.sig AS sig", REMOVED_IN: "a.kind AS kind",
                CHANGED_IN: "r.kind AS ckind, r.after AS after"}

    def by_version(job: tuple[str, int]) -> tuple[str, list[dict]]:
        rel, vid = job
        q = (f"MATCH (a)-[r:{rel}]->(v) WHERE v.id IN $ids RETURN a.id AS aid, a.library AS lib, a.path AS path, "
             f"v.ord AS ord, {rel_cols[rel]}")
        return rel, g.query(q, {"ids": [vid]})

    api_ids: set[int] = set()
    with ThreadPoolExecutor(EXPORT_THREADS) as pool:
        for rel, rows in pool.map(by_version, [(rel, v) for rel in rel_cols for v in vids]):
            for r in rows:
                if r["lib"] not in out or not r["path"] or r["ord"] is None:
                    continue
                data = out[r["lib"]]
                api_ids.add(int(r["aid"]))
                if rel == ADDED_IN:
                    data.events.setdefault(r["path"], []).append([int(r["ord"]), ADDED_IN, r["sig"] or ""])
                    data.kinds[r["path"]] = r["kind"] or ""
                elif rel == REMOVED_IN:
                    data.events.setdefault(r["path"], []).append([int(r["ord"]), REMOVED_IN, ""])
                else:
                    data.changes.setdefault(r["path"], []).append([int(r["ord"]), r["ckind"] or "", r["after"] or ""])

    def cues_into(job: tuple[str, list[int]]) -> tuple[str, list[dict]]:
        rel, ids = job
        q = (f"MATCH (t)-[r:{rel}]->(a) WHERE a.id IN $ids RETURN a.library AS lib, t.word AS word, t.key AS key, "
             "a.path AS path, r.w AS w, r.n_pass AS n_pass, r.n_fail AS n_fail")
        return rel, g.query(q, {"ids": ids})

    ids = sorted(api_ids)
    chunks = [ids[i:i + 1000] for i in range(0, len(ids), 1000)]
    with ThreadPoolExecutor(EXPORT_THREADS) as pool:
        for rel, rows in pool.map(cues_into, [(rel, c) for rel in (CUES, SHORTCUT) for c in chunks]):
            for r in rows:
                word = r["word"] or r["key"]
                if r["lib"] not in out or not word or not r["path"]:
                    continue
                data = out[r["lib"]]
                w = 1.0 if r["w"] is None else float(r["w"])
                row = data.cues.setdefault(word, {})
                row[r["path"]] = max(w, row.get(r["path"], 0.0)) if rel == SHORTCUT else w
                if rel == CUES and (r["n_pass"] or r["n_fail"]):
                    data.stats.setdefault(word, {})[r["path"]] = [int(r["n_pass"] or 0), int(r["n_fail"] or 0)]
    for data in out.values():
        for rows in (data.events, data.changes):
            for v in rows.values():
                v.sort()
    return out


def _export_episodes(g: Graph, libs: list[str]) -> list[dict]:
    where, params = _scope(libs, "e")
    q = (f"MATCH (e:{EPISODE})-[u:{USED}]->(a) {where} RETURN e.key AS key, e.library AS lib, e.version AS version, "
         "e.passed AS passed, e.error_type AS error_type, a.path AS path")
    wanted = set(libs)
    eps: dict[str, dict] = {}
    for r in g.query(q, params):
        if r["lib"] not in wanted:
            continue
        e = eps.setdefault(r["key"], {"key": r["key"], "library": r["lib"], "version": r["version"],
                                      "passed": bool(r["passed"]), "error_type": r["error_type"] or "", "used": []})
        if r["path"]:
            e["used"].append(r["path"])
    for e in eps.values():
        e["used"].sort()
    return sorted(eps.values(), key=lambda e: e["key"])
