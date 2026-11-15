"""GraphLearner: every solver attempt changes the HydraDB graph (record), and once per iteration the graph is tidied
(maintain: path compression, entity merge / split, decay). See PLAN.md section 4 and `weights.py` for the numbers."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pinned.contract import (
    API,
    CUES,
    EPISODE,
    REPLACED_BY,
    SHORTCUT,
    USED,
    W_FLOOR,
    ContextItem,
    Episode,
    Graph,
    Ingestor,
    LearnReport,
)
from pinned.graph import link_props_many, node, set_edges_many, upsert_nodes, version_ord

from . import text
from .code import CodeUse, analyse
from .errors import Gap, find_gaps
from .facts import ApiFacts, has_param, load_facts, usable
from .maintain import Maintainer, MaintainState
from .movelog import MoveLog
from .routes import api_id, episode_id, library_id, route, route_string, term_id, used_items
from .split import SplitGuard
from .weights import W_LEARNED, bump

MAX_TERMS = 48         # problem words considered for reweighting
MAX_LEARN_TERMS = 24   # problem words that may get a new learned cue on a pass
ERROR_KEEP = 300       # chars of error_text kept on the Episode node

LIVE_PREFIX = "live-"


class FairLock:
    """FIFO mutex: callers get the lock in arrival order. record() and maintain() run one at a time (a weight update
    is read-modify-write); plain threading.Lock has no order and starved some callers under load (measured)."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._next = 0
        self._serving = 0

    def __enter__(self) -> None:
        with self._cond:
            ticket = self._next
            self._next += 1
            while ticket != self._serving:
                self._cond.wait()

    def __exit__(self, *exc: object) -> None:
        with self._cond:
            self._serving += 1
            self._cond.notify_all()


class GraphLearner:
    """Implements `Learner`. `run_id` scopes episodes and names the move log (`<log_dir>/<run_id>.jsonl`);
    `split_path` (evidence/split.json) makes test-split episodes raise `TestSplitError`; None = live product use."""

    def __init__(
        self,
        graph: Graph,
        ingestor: Ingestor | None = None,
        run_id: str = "live",
        split_path: str | Path | None = None,
        *,
        log_dir: str | Path | None = None,
    ):
        self.graph = graph
        self.ingestor = ingestor
        self.run_id = run_id
        self.guard = SplitGuard(split_path)
        self.log = MoveLog(run_id, log_dir)
        self._lock = FairLock()
        self._roots: dict[str, set[str]] = {}
        self._gaps_done: set[tuple[str, str, str]] = set()
        self.state = MaintainState()

    # ------------------------------------------------------------------ Learner
    def record(self, episode: Episode) -> LearnReport:
        self.guard.check(episode.problem_id)
        with self._lock:
            return _Recorder(self, episode).run()

    def maintain(self) -> LearnReport:
        with self._lock:
            return Maintainer(self.graph, self.run_id, self.log, self.state).run()

    # ------------------------------------------------------------------ helpers shared with _Recorder
    def roots(self, library: str, extra: Iterable[str] = ()) -> set[str]:
        """Import names of a library: `Library.import_names` from the graph (cached), else the normalised name."""
        if library not in self._roots:
            row = node(self.graph, "Library", library_id(library), ("import_names",))
            names = {x.strip() for x in str((row or {}).get("import_names") or "").split(",") if x.strip()}
            self._roots[library] = names or {library.replace("-", "_")}
        return self._roots[library] | set(extra)

    def existing(self, library: str, paths: Iterable[str]) -> dict[str, int]:
        """Which of these dotted paths are Api nodes in the graph: {path: id}."""
        want = {api_id(library, p): p for p in paths}
        if not want:
            return {}
        rows = self.graph.query(f"MATCH (a:{API}) WHERE a.id IN $ids RETURN a.id AS id", {"ids": list(want)})
        return {want[r["id"]]: r["id"] for r in rows if r["id"] in want}


@dataclass
class _EdgeRow:
    rel: str
    src: int
    dst: int
    src_key: str
    dst_key: str
    w: float | None
    n_pass: int
    n_fail: int
    extra: dict = field(default_factory=dict)


class _Recorder:
    """One `record()` call. Order: guard -> analyse code -> reweight -> learned cues -> gaps -> provenance."""

    def __init__(self, learner: GraphLearner, ep: Episode):
        self.L, self.g, self.ep = learner, learner.graph, ep
        self.lib = ep.library.strip().lower()
        try:
            self.ord = version_ord(ep.version)
        except ValueError as e:
            raise ValueError(f"episode version {ep.version!r} is not a valid version: {e}") from None
        pid = ep.problem_id or LIVE_PREFIX + hashlib.sha1(
            f"{self.lib}|{ep.version}|{ep.problem}|{ep.code}".encode()
        ).hexdigest()[:12]
        self.key = f"{learner.run_id}|{pid}|{ep.iteration}"
        self.pid = pid
        self.report = LearnReport()
        self.touched: set[tuple[str, int, int]] = set()

    # ---------------------------------------------------------------- driver
    def run(self) -> LearnReport:
        ep = self.ep
        if node(self.g, EPISODE, episode_id(self.key)) is not None:
            self.report.notes.append(f"episode {self.key} already recorded; nothing changed")
            return self.report
        ctx_roots = {it.path.split(".", 1)[0] for it in (ep.context.items if ep.context else [])}
        roots = self.L.roots(self.lib, ctx_roots)
        use = analyse(ep.code, roots)
        if not use.parsed:
            self.report.notes.append(f"code did not parse ({use.error}); only provenance and gaps recorded")
        words = text.terms(ep.problem, limit=MAX_TERMS)
        items = used_items(ep.context, use)
        code_paths = {p for p in use.paths if p not in use.modules}
        exist = self.L.existing(self.lib, code_paths | {it.path for it in items})
        if ep.context is not None:
            targets = {it.path for it in items if it.path in exist}
        else:
            targets = {p for p in code_paths if p in exist}
            self.report.notes.append("no context given: learning from the APIs the code called")

        self._reweight(words, targets, items, exist)
        if ep.passed:
            self._learn_cues(words[:MAX_LEARN_TERMS], {p for p in code_paths | {it.path for it in items} if p in exist}, exist)
        gaps = self._gaps(use, roots)
        self._provenance(words, items, exist, code_paths, gaps)
        return self.report

    # ---------------------------------------------------------------- reads
    def _edges(self, rel: str, src_ids: list[int], dst_ids: list[int], extra: tuple[str, ...] = ()) -> list[dict]:
        if not src_ids or not dst_ids:
            return []
        cols = ", ".join(["a.id AS src", "b.id AS dst", "a.key AS src_key", "b.key AS dst_key", "r.w AS w",
                          "r.n_pass AS n_pass", "r.n_fail AS n_fail"] + [f"r.{p} AS {p}" for p in extra])
        q = f"MATCH (a)-[r:{rel}]->(b) WHERE a.id IN $s AND b.id IN $d RETURN {cols}"
        return self.g.query(q, {"s": list(dict.fromkeys(src_ids)), "d": list(dict.fromkeys(dst_ids))})

    # ---------------------------------------------------------------- moves
    def _why(self, what: str) -> str:
        ep = self.ep
        outcome = "pass" if ep.passed else f"fail ({ep.error_type or 'error'})"
        return f"{outcome} at {self.lib}=={ep.version}: {what}"

    def _apply(self, rows: list[_EdgeRow], move: str, why: str) -> int:
        """Bump every row by the episode outcome, write them batched per relationship, log each move."""
        passed = self.ep.passed
        by_rel: dict[str, list[tuple[int, int, dict]]] = {}
        for r in rows:
            after = bump(r.w, passed)
            props = {"w": after, "n_pass": r.n_pass + int(passed), "n_fail": r.n_fail + int(not passed)}
            by_rel.setdefault(r.rel, []).append((r.src, r.dst, props))
            self.touched.add((r.rel, r.src, r.dst))
            self.L.state.touched[r.rel].add((r.src, r.dst))
            self.L.log.write(move, {"src": r.src_key, "rel": r.rel, "dst": r.dst_key, **r.extra},
                             {"w": r.w, "n_pass": r.n_pass, "n_fail": r.n_fail}, props, why, [self.key])
        for rel, batch in by_rel.items():
            set_edges_many(self.g, rel, batch)
        return len(rows)

    def _reweight(self, words: list[str], targets: set[str], items: list[ContextItem], exist: dict[str, int]) -> None:
        """Edge reweighting: CUES / SHORTCUT from the problem's words to the used APIs, REPLACED_BY along their chains."""
        tids = [term_id(w) for w in words]
        aids = [exist[p] for p in targets]
        rows: list[_EdgeRow] = []
        for rel in (CUES, SHORTCUT):
            extra = ("lo_ord", "hi_ord") if rel == SHORTCUT else ()
            for e in self._edges(rel, tids, aids, extra):
                if rel == SHORTCUT and not (int(e["lo_ord"] or 0) <= self.ord <= int(e["hi_ord"] or 0)):
                    continue
                rows.append(_EdgeRow(rel, e["src"], e["dst"], e["src_key"], e["dst_key"], e["w"],
                                     int(e["n_pass"] or 0), int(e["n_fail"] or 0)))
        pairs = {
            (api_id(self.lib, h.src), api_id(self.lib, h.dst))
            for it in items if it.path in targets
            for h in route(it) if h.rel == REPLACED_BY
        }
        if pairs:
            for e in self._edges(REPLACED_BY, [s for s, _ in pairs], [d for _, d in pairs]):
                if (e["src"], e["dst"]) in pairs:
                    rows.append(_EdgeRow(REPLACED_BY, e["src"], e["dst"], e["src_key"], e["dst_key"], e["w"],
                                         int(e["n_pass"] or 0), int(e["n_fail"] or 0)))
        if rows:
            used = ", ".join(sorted(targets))
            self.report.edges_reweighted += self._apply(rows, "reweight", self._why(f"code used {used}"))

    def _learn_cues(self, words: list[str], targets: set[str], exist: dict[str, int]) -> None:
        """Learned cues (pass only): raise existing CUES from the problem's words to the APIs the passing code used, or
        add new CUES{source: learned}. Source = problem text + passing code; never the answer key."""
        if not words or not targets:
            return
        tids = {term_id(w): w for w in words}
        aids = {exist[p]: p for p in targets}
        have = {(e["src"], e["dst"]): e for e in self._edges(CUES, list(tids), list(aids))}
        raise_rows: list[_EdgeRow] = []
        new_rows: list[tuple[int, int, dict]] = []
        new_terms: dict[int, str] = {}
        for tid, word in tids.items():
            for aid, path in aids.items():
                if (CUES, tid, aid) in self.touched:
                    continue
                e = have.get((tid, aid))
                if e is not None:
                    raise_rows.append(_EdgeRow(CUES, tid, aid, word, f"{self.lib}|{path}", e["w"],
                                               int(e["n_pass"] or 0), int(e["n_fail"] or 0)))
                else:
                    new_rows.append((tid, aid, {"w": W_LEARNED, "source": "learned", "n_pass": 1, "n_fail": 0}))
                    new_terms[tid] = word
        why = self._why("passing code used " + ", ".join(sorted(targets)))
        if raise_rows:
            self.report.edges_reweighted += self._apply(raise_rows, "learn_cue", why)
        if new_rows:
            upsert_nodes(self.g, "Term", [{"key": w, "word": w} for w in new_terms.values()])
            link_props_many(self.g, CUES, new_rows)
            for tid, aid, props in new_rows:
                self.touched.add((CUES, tid, aid))
                self.L.log.write("learn_cue", {"src": tids[tid], "rel": CUES, "dst": f"{self.lib}|{aids[aid]}"},
                                 None, props, why, [self.key])
            self.report.notes.append(f"learned cues added: {len(new_rows)}")

    # ---------------------------------------------------------------- gap-driven ingestion
    def _gaps(self, use: CodeUse, roots: set[str]) -> list[Gap]:
        ep = self.ep
        if not (ep.error_type or ep.error_text):
            return []
        gaps = find_gaps(ep.error_type, ep.error_text, use, roots, lambda c: set(self.L.existing(self.lib, c)))
        if not gaps:
            return []
        exist = self.L.existing(self.lib, [gp.path for gp in gaps])
        facts = load_facts(self.g, list(exist.values()))
        for gp in gaps:
            f = facts.get(exist[gp.path]) if gp.path in exist else None
            reason = _gap_reason(gp, f, self.ord)
            keys = {"library": self.lib, "version": ep.version, "hint": gp.path, "kind": gp.kind}
            if reason is None:
                self.L.log.write("gap_known", keys, None, None,
                                 self._why(f"graph already had the right fact for {gp.path}: {gp.message}"), [self.key])
                continue
            why = self._why(f"{reason}: {gp.message}")
            done_key = (self.lib, ep.version, gp.path)
            if self.L.ingestor is None:
                self.L.log.write("gap_open", keys, None, None, why + " (no ingestor attached)", [self.key])
                self.report.notes.append(f"gap not filled (no ingestor): {gp.path}")
                continue
            if done_key in self.L._gaps_done:
                self.report.notes.append(f"gap already filled this run: {gp.path}")
                continue
            try:
                rep = self.L.ingestor.fill_gap(self.lib, ep.version, gp.path)
            except Exception as e:  # an ingestion failure must not lose the episode; it is logged and reported
                self.L.log.write("gap_failed", keys, None, {"error": f"{type(e).__name__}: {e}"[:500]}, why, [self.key])
                self.report.notes.append(f"fill_gap failed for {gp.path}: {type(e).__name__}: {e}")
                continue
            self.L._gaps_done.add(done_key)
            self.L.state.dirty_libs.add(self.lib)  # new facts: entity merge must look at this library again
            self.report.gaps_ingested += 1
            self.L.log.write("gap_fill", keys, None, asdict(rep) if hasattr(rep, "__dataclass_fields__") else str(rep),
                             why, [self.key])
        return gaps

    # ---------------------------------------------------------------- provenance
    def _provenance(self, words: list[str], items: list[ContextItem], exist: dict[str, int], code_paths: set[str],
                    gaps: list[Gap]) -> None:
        """Episode node + USED{helped} edges. USED also keeps the chain the item came by (`via`) and the problem words
        that cue its first API (`cue_terms`), which path compression reads in maintain()."""
        ep = self.ep
        err_api = next((gp.path for gp in gaps if gp.kind in ("keyword", "signature")), gaps[0].path if gaps else "")
        props = {
            "key": self.key, "run_id": self.L.run_id, "problem_id": self.pid, "library": self.lib,
            "version": ep.version, "ord": self.ord, "passed": bool(ep.passed), "error_type": ep.error_type or "",
            "iteration": int(ep.iteration), "error_api": err_api, "error": (ep.error_text or "")[:ERROR_KEEP],
            "with_context": ep.context is not None,
        }
        eid = upsert_nodes(self.g, EPISODE, [props])[0]

        via: dict[str, str] = {}
        starts: dict[str, str] = {}
        for it in items:
            hops = route(it)
            if hops and it.path in exist:
                via[it.path] = route_string(hops)
                starts[it.path] = hops[0].src
        cue_terms: dict[str, list[str]] = {}
        if starts:
            tids = {term_id(w): w for w in words}
            sids = {api_id(self.lib, s): s for s in set(starts.values())}
            cued: dict[str, list[str]] = {}
            for e in self._edges(CUES, list(tids), list(sids)):
                if e["w"] is not None and e["w"] >= W_FLOOR:
                    cued.setdefault(sids[e["dst"]], []).append(tids[e["src"]])
            for path, start in starts.items():
                cue_terms[path] = sorted(cued.get(start, []))

        used = sorted({p for p in code_paths if p in exist} | {it.path for it in items if it.path in exist})
        rows = []
        for p in used:
            ep_props: dict = {"helped": bool(ep.passed)}
            if p in via:
                ep_props["via"] = via[p]
                ep_props["cue_terms"] = ",".join(cue_terms.get(p, []))
            rows.append((eid, exist[p], ep_props))
        if rows:
            link_props_many(self.g, USED, rows)
        self.L.log.write("episode", {"node": self.key}, None,
                         {"passed": bool(ep.passed), "used": used, "via": via, "error_api": err_api},
                         self._why(f"{len(used)} APIs used"), [self.key])


def _gap_reason(gp: Gap, f: ApiFacts | None, ord_: int) -> str | None:
    """Why the graph's facts are missing or wrong for this failure, or None if the graph already had it right."""
    if f is None:
        return f"graph has no Api node for {gp.path}"
    if not f.events:
        return f"graph has no version facts for {gp.path}"
    status = f.status_at(ord_)
    if gp.kind in ("attribute", "import"):
        return f"graph says {gp.path} is {status} here, but the runtime could not find it" if usable(status) else None
    if gp.kind == "keyword":
        sig = f.signature_at(ord_)
        if not sig:
            return f"graph has no signature for {gp.path} here"
        return f"graph signature {sig} has '{gp.detail}' but the runtime rejected it" if has_param(sig, gp.detail) else None
    if gp.kind == "signature":
        return None if f.signature_at(ord_) else f"graph has no signature for {gp.path} here"
    if gp.kind == "deprecated":
        return None if f.deprecated_at(ord_) else f"graph has no deprecation fact for {gp.path} here"
    return None
