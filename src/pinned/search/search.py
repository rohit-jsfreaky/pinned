"""GraphSearch: the `Search` of contract.py over the HydraDB graph.

context_for  : candidates (code + task words via CUES/SHORTCUT, scored by learned w) -> status at v (one batched read)
               -> for anything not usable, SSpaths walks over REPLACED_BY / ALIAS_OF to the strongest API valid at v.
check_calls  : every call/attribute of the library in the code -> status at v, fix + chain, bad keyword arguments.
explain_api  : status + signature at every ingested version, plus the replacement chain out of the API.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from packaging.version import InvalidVersion

from pinned.contract import (
    ADDED_IN,
    API,
    CHANGED_IN,
    ApiTimeline,
    Context,
    ContextItem,
    Finding,
    Graph,
    Hop,
    Status,
    TERM,
    VersionFact,
)
from pinned.graph import node, node_id, version_ord

from . import facts as F
from .chains import Replacement, best_replacement, chain_hops, is_rename, version_hop
from .code import CodeRefs, read_code
from .ranking import (
    CODE_SCORE,
    CODE_WORD_Q,
    GUESS_SCORE,
    LOOSE_Q,
    TASK_Q,
    Candidate,
    apply_member_boost,
    ranked,
    score_cues,
)
from .signature import keyword_fix, parse_params
from .text import dotted_names, words
from .validity import Event, Verdict, verdict_at

MAX_CODE_CANDIDATES = 48
HUB_POOL = 24         # candidates hub words re-weight
SHORTLIST = 16        # candidates whose version facts are read (plus every code reference)
MAX_WORDS = 64
CUE_HOPS = 2          # strongest Term edges kept in an item's chain (the evidence for why it was picked)
WALK_STATUSES = ("removed", "missing", "added_later", "deprecated")


@dataclass
class _Scope:
    """One resolved (library, version) for one call."""

    g: F.CountingGraph
    meta: F.LibraryMeta
    version: str
    ord: int


class _Unknown(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class GraphSearch:
    """`Search` over a HydraDB graph. Thread-safe: share one instance across requests."""

    def __init__(self, graph: Graph, workers: int = 8):
        self.graph = graph
        self.stats = F.TermStats()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pinned-search")

    def close(self) -> None:
        self._pool.shutdown(wait=False)

    # ------------------------------------------------------------------------------------------------ scope
    def _scope(self, g: F.CountingGraph, library: str, version: str) -> _Scope:
        lib = (library or "").strip().lower()
        ver = (version or "").strip()
        if not lib:
            raise _Unknown("No library given.")
        try:
            ord_v = version_ord(ver)
        except (InvalidVersion, ValueError):
            raise _Unknown(f"'{ver}' is not a valid version number.") from None
        meta = F.library_meta(g, lib)
        if meta is None:
            raise _Unknown(f"Pinned has no data for the library '{lib}'.")
        row = meta.find(ver, ord_v)
        if row is None:
            known = ", ".join(v.version for v in meta.versions) or "none yet"
            raise _Unknown(f"Pinned has no data for {lib}=={ver}. Ingested versions: {known}.")
        return _Scope(g, meta, row.version, row.ord)

    # ------------------------------------------------------------------------------------------------ shared steps
    def _walk(self, s: _Scope, verdicts: dict[int, Verdict], seeds: list[int],
              known: dict[int, F.ApiFacts]) -> dict[int, Replacement]:
        """Replacement for every seed: SSpaths out of each seed that has REPLACED_BY / ALIAS_OF edges out (in parallel),
        SSpaths into each seed newer than v (or unknown) that has such edges in, then one batched events read for the
        nodes the incoming walks found. `known` must come from `facts.events` (it carries the chain-edge flags)."""
        out_seeds = [a for a in seeds if a in known and known[a].chains_out]
        back_seeds = [a for a in seeds if a in known and known[a].chains_in
                      and verdicts[a].status in ("added_later", "missing")]
        if not out_seeds and not back_seeds:
            return {}
        outs = list(self._pool.map(lambda a: F.walk_out(s.g, a), out_seeds))
        ins = list(self._pool.map(lambda a: F.walk_in(s.g, a), back_seeds))
        pool: dict[int, F.ApiFacts] = dict(known)
        for w in outs:
            pool.update(w.facts)
        need = [p.nodes[-1]["id"] for w in ins for p in w.chains if p.nodes[-1]["id"] not in pool]
        if need:
            pool.update(F.events(s.g, need))
        result: dict[int, Replacement] = {}
        for seed in seeds:
            walks = [w for w in outs if w.seed == seed] + [w for w in ins if w.seed == seed]
            rep = best_replacement(walks, pool, s.ord)
            if rep is not None:
                result[seed] = rep
        return result

    def _status(self, seed_path: str, v: Verdict, rep: Replacement | None) -> Status:
        if v.status == "removed" and rep is not None and is_rename(seed_path, rep):
            return "renamed"
        return v.status

    def _chain(self, s: _Scope, path: str, v: Verdict, rep: Replacement | None) -> list[Hop]:
        hops = version_hop(s.meta, path, v, s.version)
        if rep is not None:
            hops += rep.hops
            d = rep.verdict.deciding
            if d is not None:
                hops.append(Hop(rep.path, d.rel, s.meta.label(d.version), f"valid in {s.version}"))
        return hops

    def _note(self, s: _Scope, path: str, status: Status, v: Verdict, rep: Replacement | None) -> str:
        lib, ver = s.meta.name, s.version
        use = f" Use {rep.path} instead." if rep else ""
        d = v.deciding
        if status == "valid":
            return f"Available in {lib} {ver}" + (f" (since {d.version})." if d else ".")
        if status == "deprecated":
            e = v.deprecation
            since = f" since {e.version}" if e else ""
            msg = f" {e.after or e.evidence}".rstrip() if e else ""
            return f"Deprecated{since} (still works in {ver}).{msg}" + (f" Prefer {rep.path}." if rep else "")
        if status in ("removed", "renamed"):
            where = f" in {d.version}" if d else ""
            word = "Renamed/moved" if status == "renamed" else "Removed"
            return f"{word}{where}: not available in {lib} {ver}." + (use or " No replacement is known in the graph.")
        if status == "added_later":
            when = f" in {d.version}" if d else " later"
            return f"Added{when}, after {ver}: not available in {lib} {ver}." + use
        return f"Not found in any ingested {lib} version." + use

    # ------------------------------------------------------------------------------------------------ context_for
    def context_for(self, problem: str, starting_code: str, library: str, version: str, k: int = 8) -> Context:
        t0 = time.perf_counter()
        g = F.CountingGraph(self.graph)
        try:
            s = self._scope(g, library, version)
        except _Unknown as e:
            return self._unknown_context(library, version, e.message, g, t0)
        k = max(1, int(k))
        code = read_code(starting_code, s.meta.import_names)
        cands: dict[int, Candidate] = {}

        used = {r.path for r in code.refs if r.kind != "import"}
        # an import the code also uses ("from scipy.special import i1" + "i1(x)", or "from scipy import special" +
        # "special.i1(x)") adds nothing on its own; an unused import still counts (it can fail at import time)
        refs = [r for r in code.refs
                if r.kind != "import" or (r.path not in used and not any(u.startswith(r.path + ".") for u in used))]
        for ref in refs[:MAX_CODE_CANDIDATES]:
            aid = F.api_id(s.meta.name, ref.path)
            c = cands.setdefault(aid, Candidate(aid, ref.path))
            if not c.from_code:
                c.from_code, c.score = True, c.score + CODE_SCORE
        for path in self._guess_paths(problem, code, s.meta.import_names):
            aid = F.api_id(s.meta.name, path)
            c = cands.setdefault(aid, Candidate(aid, path, guessed=True))
            if not c.from_code and c.score < GUESS_SCORE:
                c.score += GUESS_SCORE

        q: dict[str, float] = {}
        for w in code.words:
            q[w] = max(q.get(w, 0.0), CODE_WORD_Q)
        for w in code.loose:
            q[w.lower()] = max(q.get(w.lower(), 0.0), LOOSE_Q)
        for w in words(problem):
            q[w] = max(q.get(w, 0.0), TASK_Q)
        terms = sorted(q, key=lambda t: (-q[t], t))[:MAX_WORDS]
        cue_rows, hubs = F.cues(g, s.meta.name, terms, s.ord, self.stats) if terms else ([], [])
        degree = {t: self.stats.degree(node_id(TERM, t), s.meta.name) for t in terms}
        score_cues(cands, cue_rows, q, degree)
        if hubs:  # hub words only re-weight the strongest candidates found so far (read from the Api side: cheap)
            pool = self._shortlist(cands, max(3 * k, HUB_POOL))
            score_cues(cands, F.hub_cues(g, hubs, [c.api for c in pool]), q, degree)
        apply_member_boost(cands, {r.path for r in code.refs})

        shortlist = self._shortlist(cands, max(2 * k, SHORTLIST))
        known = F.events(g, [c.api for c in shortlist])

        picked: list[tuple[Candidate, Verdict]] = []
        for c in shortlist:
            f = known.get(c.api)
            evs = f.events if f else []
            v = verdict_at(evs, s.ord)
            if not evs and (c.guessed or not c.from_code):
                continue  # a guess or a cue target the graph has no version facts for: nothing to tell the agent
            picked.append((c, v))
        picked.sort(key=lambda cv: (-cv[0].score, cv[0].path))
        picked = picked[:k]

        verdicts = {c.api: v for c, v in picked}
        seeds = [c.api for c, v in picked if v.status in WALK_STATUSES]
        reps = self._walk(s, verdicts, seeds, known)

        items: list[ContextItem] = []
        seen: set[str] = set()
        for c, v in picked:
            rep = reps.get(c.api)
            status = self._status(c.path, v, rep)
            chain = self._cue_hops(c) + self._chain(s, c.path, v, rep)
            items.append(ContextItem(s.meta.name, c.path, status, chain, v.signature if v.usable else "",
                                     self._note(s, c.path, status, v, rep), round(c.score, 4)))
            seen.add(c.path)
            if rep is not None and rep.path not in seen:
                seen.add(rep.path)
                rhops = rep.hops + version_hop(s.meta, rep.path, rep.verdict, s.version)
                items.append(ContextItem(s.meta.name, rep.path, rep.verdict.status, rhops, rep.verdict.signature,
                                         f"Use this instead of {c.path} in {s.meta.name} {s.version}.",
                                         round(c.score * 0.999, 4)))
        deduped: dict[str, ContextItem] = {}
        for it in items:  # a replacement that is also a direct candidate keeps its higher-scored entry
            if it.path not in deduped or it.score > deduped[it.path].score:
                deduped[it.path] = it
        final = sorted(deduped.values(), key=lambda it: (-it.score, it.path))[:k]
        return Context(s.meta.name, s.version, final, round((time.perf_counter() - t0) * 1000, 2), g.requests)

    @staticmethod
    def _shortlist(cands: dict[int, Candidate], size: int) -> list[Candidate]:
        """The `size` best candidates plus every candidate the code references."""
        top = ranked(cands)[:size]
        ids = {c.api for c in top}
        return top + [c for c in cands.values() if c.from_code and c.api not in ids]

    @staticmethod
    def _cue_hops(c: Candidate) -> list[Hop]:
        best = sorted(c.cues, key=lambda x: (-x[0], x[1].term))[:CUE_HOPS]
        return [Hop(cue.term, cue.rel, c.path, f"w={cue.w:.2f}") for _, cue in best]

    @staticmethod
    def _guess_paths(problem: str, code: CodeRefs, roots: set[str]) -> list[str]:
        """Dotted names in the task text ("DataFrame.append", "np.linalg.norm") as import paths of this library."""
        out: list[str] = []
        for name in dotted_names(problem):
            head, _, rest = name.partition(".")
            if head in code.aliases:
                out.append(f"{code.aliases[head]}.{rest}")
            elif head in roots:
                out.append(name)
            else:
                out += [f"{r}.{name}" for r in sorted(roots)]
        return list(dict.fromkeys(out))

    def _unknown_context(self, library: str, version: str, message: str, g: F.CountingGraph, t0: float) -> Context:
        lib = (library or "").strip().lower()
        item = ContextItem(lib, lib, "unknown", [], "", message, 0.0)
        return Context(lib, (version or "").strip(), [item], round((time.perf_counter() - t0) * 1000, 2), g.requests)

    # ------------------------------------------------------------------------------------------------ check_calls
    def check_calls(self, code: str, library: str, version: str) -> list[Finding]:
        g = F.CountingGraph(self.graph)
        try:
            s = self._scope(g, library, version)
        except _Unknown as e:
            return [Finding(call=(library or "").strip().lower(), status="unknown", fix=e.message)]
        parsed = read_code(code, s.meta.import_names)
        if parsed.error:
            return [Finding(call="<code>", status="unknown", fix=f"The code does not parse: {parsed.error}",
                            line=parsed.error_line)]
        if not parsed.refs:
            return []
        ids = {r.path: F.api_id(s.meta.name, r.path) for r in parsed.refs}
        known = F.events(g, list(ids.values()))
        verdicts = {aid: verdict_at(known[aid].events if aid in known else [], s.ord) for aid in ids.values()}
        seeds = [aid for aid, v in verdicts.items() if v.status in WALK_STATUSES]
        reps = self._walk(s, verdicts, seeds, known)

        findings: list[Finding] = []
        for ref in parsed.refs:
            aid = ids[ref.path]
            v, rep = verdicts[aid], reps.get(aid)
            if ref.inferred and v.status == "missing" and rep is None:
                # the object's type was inferred; the member may be inherited or dynamic - do not claim it is missing
                findings.append(Finding(ref.path, "unknown", f"No record of {ref.path} in the graph (type was inferred "
                                        "from the code; it may be inherited).", [], ref.line))
                continue
            status = self._status(ref.path, v, rep)
            findings.append(Finding(ref.path, status, rep.path if rep else "", self._chain(s, ref.path, v, rep), ref.line))
            if ref.kind == "call" and ref.keywords and v.usable:
                findings += self._keyword_findings(s, ref.path, ref.keywords, ref.line, v, known[aid].events)
        return findings

    def _keyword_findings(self, s: _Scope, path: str, keywords: tuple[str, ...], line: int, v: Verdict,
                          events: list[Event]) -> list[Finding]:
        params = parse_params(v.signature)
        out = []
        for kw in keywords:
            if params.accepts(kw):
                continue
            fix = keyword_fix(kw, events, params)
            chain: list[Hop] = []
            if fix.event is not None:
                e = fix.event
                before = e.before if e.rel == CHANGED_IN else ""
                after = e.after if e.rel == CHANGED_IN else e.sig
                chain.append(Hop(path, e.rel, s.meta.label(e.version), f"{before} -> {after}".strip(" ->")))
            if fix.replacement:
                text = f"use {fix.replacement}= instead of {kw}= ({s.meta.name} {s.version})"
            else:
                text = f"remove {kw}=: not a parameter of {path} in {s.meta.name} {s.version}; signature {v.signature}"
            out.append(Finding(f"{path}({kw}=...)", "changed", text, chain, line))
        return out

    # ------------------------------------------------------------------------------------------------ explain_api
    def explain_api(self, library: str, path: str) -> ApiTimeline:
        g = F.CountingGraph(self.graph)
        lib = (library or "").strip().lower()
        path = (path or "").strip()
        meta = F.library_meta(g, lib) if lib else None
        if meta is None or not path:
            return ApiTimeline(lib, path, graph_queries=g.requests)
        aid = F.api_id(lib, path)
        walk = F.walk_out(g, aid)
        seed = walk.facts.get(aid)
        kind = seed.kind if seed else ""
        if seed is None:
            api = node(g, API, aid, ("kind",))
            kind = str((api or {}).get("kind") or "")
        evs = seed.events if seed else []
        timeline = []
        for row in meta.versions:
            v = verdict_at(evs, row.ord)
            timeline.append(VersionFact(row.version, row.ord, v.status, v.signature,
                                        self._version_note(evs, row.ord)))
        hops: list[Hop] = []
        seen: set[tuple[str, str, str]] = set()
        for p in sorted(walk.chains, key=lambda p: (len(p.rels), [n.get("path") or "" for n in p.nodes])):
            for h in chain_hops(p):
                key = (h.src, h.rel, h.dst)
                if key not in seen:
                    seen.add(key)
                    hops.append(h)
        return ApiTimeline(lib, path, kind, timeline, hops, g.requests)

    @staticmethod
    def _version_note(events: list[Event], ord_v: int) -> str:
        notes = []
        for e in events:
            if e.ord != ord_v:
                continue
            if e.rel == CHANGED_IN:
                if e.kind == "signature":
                    notes.append(f"signature changed: {e.before} -> {e.after}")
                elif e.kind == "deprecated":
                    notes.append(f"deprecated: {e.after or e.evidence}".rstrip(": "))
                else:
                    notes.append(f"{e.kind or 'changed'}: {e.after or e.evidence}".rstrip(": "))
            else:
                notes.append("added" if e.rel == ADDED_IN else "removed")
        return "; ".join(notes)
