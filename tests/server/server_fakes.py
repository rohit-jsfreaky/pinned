"""Test-only stand-ins for parts C (search) and D (learn), so the server can be tested before those parts merge.

They are deliberately small but REAL: they read and write the seeded HydraDB graph through pinned.graph helpers, using
the contract schema and validity rule, so endpoint/MCP/CLI tests exercise real graph round trips.
`build_services` matches PINNED_FACTORY's signature (settings -> (search, learner, graph)) for the live run.
"""

from __future__ import annotations

import re
import socket
import time

from pinned.contract import (
    ADDED_IN,
    API,
    CHANGED_IN,
    CUES,
    EPISODE,
    HAS_VERSION,
    LIBRARY,
    NEXT,
    REMOVED_IN,
    REPLACED_BY,
    TERM,
    USED,
    VERSION,
    W_DEFAULT,
    W_FLOOR,
    ApiTimeline,
    Context,
    ContextItem,
    Episode,
    Finding,
    Hop,
    LearnReport,
    VersionFact,
)
from pinned.graph import (
    HydraGraph,
    edges_from,
    link_many,
    link_props_many,
    node_id,
    set_edges_many,
    upsert_nodes,
    version_ord,
)

VERSIONS = ["1.0.0", "1.5.0", "2.0.0"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def seed_graph(g: HydraGraph, lib: str) -> dict[str, int]:
    """A tiny graph for `lib`: f.old_sum (1.0, removed 2.0, REPLACED_BY f.total), f.total (added 1.5, signature change
    in 2.0), f.mean (always there). Terms cue them. Returns Api ids by path."""
    upsert_nodes(g, LIBRARY, [{"key": lib, "name": lib, "import_names": lib}])
    vids = upsert_nodes(g, VERSION, [
        {"key": f"{lib}|{v}", "library": lib, "version": v, "ord": version_ord(v), "released": f"202{i}-01-01"}
        for i, v in enumerate(VERSIONS)
    ])
    v = dict(zip(VERSIONS, vids))
    link_many(g, HAS_VERSION, [(node_id(LIBRARY, lib), x) for x in vids])
    link_many(g, NEXT, list(zip(vids, vids[1:])))
    paths = [f"{lib}.old_sum", f"{lib}.total", f"{lib}.mean"]
    ids = upsert_nodes(g, API, [{"key": f"{lib}|{p}", "library": lib, "path": p, "kind": "function"} for p in paths])
    a = dict(zip(paths, ids))
    link_props_many(g, ADDED_IN, [
        (a[f"{lib}.old_sum"], v["1.0.0"], {"evidence": "seed", "sig": "(x)"}),
        (a[f"{lib}.total"], v["1.5.0"], {"evidence": "seed", "sig": "(x)"}),
        (a[f"{lib}.mean"], v["1.0.0"], {"evidence": "seed", "sig": "(x, axis=None)"}),
    ])
    link_props_many(g, REMOVED_IN, [(a[f"{lib}.old_sum"], v["2.0.0"], {"evidence": "seed"})])
    link_props_many(g, CHANGED_IN, [(a[f"{lib}.total"], v["2.0.0"],
                                     {"kind": "signature", "before": "(x)", "after": "(x, *, skipna=True)",
                                      "evidence": "seed"})])
    link_props_many(g, REPLACED_BY, [(a[f"{lib}.old_sum"], a[f"{lib}.total"],
                                      {"w": W_DEFAULT, "evidence": "seed", "since_ord": version_ord("2.0.0")})])
    words = {"sum": [f"{lib}.old_sum", f"{lib}.total"], "total": [f"{lib}.total"], "average": [f"{lib}.mean"],
             "mean": [f"{lib}.mean"]}
    tids = upsert_nodes(g, TERM, [{"key": f"{lib}:{w}", "word": w} for w in words])
    link_props_many(g, CUES, [(t, a[p], {"w": W_DEFAULT, "source": "doc"})
                              for t, (w, ps) in zip(tids, words.items()) for p in ps])
    return a


class GraphBackedSearch:
    """Search over the seeded graph. Terms are stored as "<lib>:<word>" so parallel tests never share them."""

    def __init__(self, graph: HydraGraph):
        self.g = graph

    def _events(self, api_id: int) -> list[tuple[int, str, str, dict]]:
        out = []
        for rel in (ADDED_IN, REMOVED_IN, CHANGED_IN):
            props = {"ADDED_IN": ("sig",), "REMOVED_IN": (), "CHANGED_IN": ("kind", "after")}[rel]
            for r in edges_from(self.g, [api_id], rel, props=props, dst_props=("ord", "version")):
                out.append((r["dst_ord"], rel, r["dst_version"], r))
        return sorted(out, key=lambda e: e[0])

    def _status(self, lib: str, path: str, ver: str) -> tuple[str, str, list[Hop], str]:
        """-> (status, signature, chain, fix) at ver, by the contract's validity rule."""
        aid = node_id(API, f"{lib}|{path}")
        events = self._events(aid)
        if not events:
            return "missing", "", [], ""
        o = version_ord(ver)
        life = [e for e in events if e[1] in (ADDED_IN, REMOVED_IN) and e[0] <= o]
        sig = ""
        for e in events:
            if e[0] <= o and e[1] == ADDED_IN:
                sig = e[3].get("sig") or sig
            if e[0] <= o and e[1] == CHANGED_IN and e[3].get("kind") == "signature":
                sig = e[3].get("after") or sig
        if life and life[-1][1] == ADDED_IN:
            return "valid", sig, [], ""
        if not life:
            return "added_later", "", [Hop(path, ADDED_IN, f"{lib} {events[0][2]}")], ""
        chain = [Hop(path, REMOVED_IN, f"{lib} {life[-1][2]}")]
        fix = ""
        for r in edges_from(self.g, [aid], REPLACED_BY, props=("w",), dst_props=("path",)):
            if (r.get("w") or 0) >= W_FLOOR:
                chain.append(Hop(path, REPLACED_BY, r["dst_path"]))
                fix = r["dst_path"]
        return "removed", "", chain, fix

    def check_calls(self, code: str, library: str, version: str) -> list[Finding]:
        out = []
        pat = re.compile(rf"\b{re.escape(library)}\.([A-Za-z_][\w.]*)\s*\(")
        for n, line in enumerate(code.splitlines(), 1):
            for m in pat.finditer(line):
                path = f"{library}.{m.group(1)}"
                status, _, chain, fix = self._status(library, path, version)
                out.append(Finding(call=path, status=status, fix=fix, chain=chain, line=n))
        return out

    def context_for(self, problem: str, starting_code: str, library: str, version: str, k: int = 8) -> Context:
        t0, q0 = time.perf_counter(), self.g.requests
        words = sorted({w for w in re.findall(r"[a-z]+", problem.lower())})
        tids = [node_id(TERM, f"{library}:{w}") for w in words]
        scores: dict[str, float] = {}
        for r in edges_from(self.g, tids, CUES, props=("w",), dst_props=("path",)) if tids else []:
            if (r.get("w") or 0) >= W_FLOOR:
                scores[r["dst_path"]] = scores.get(r["dst_path"], 0.0) + r["w"]
        items = []
        for path, score in sorted(scores.items(), key=lambda kv: -kv[1])[:k]:
            status, sig, chain, fix = self._status(library, path, version)
            note = f"use {fix} instead" if fix else ""
            items.append(ContextItem(library, path, status, chain, sig, note, round(score, 3)))
        return Context(library, version, items, round((time.perf_counter() - t0) * 1000, 2), self.g.requests - q0)

    def explain_api(self, library: str, path: str) -> ApiTimeline:
        q0 = self.g.requests
        rows = self.g.query(f"MATCH (v:{VERSION}) WHERE v.library = $lib RETURN v.version AS version, v.ord AS ord",
                            {"lib": library})
        versions = []
        for r in sorted(rows, key=lambda r: r["ord"]):
            status, sig, _, _ = self._status(library, path, r["version"])
            versions.append(VersionFact(r["version"], r["ord"], status, sig))
        aid = node_id(API, f"{library}|{path}")
        repl = [Hop(path, REPLACED_BY, r["dst_path"]) for r in edges_from(self.g, [aid], REPLACED_BY,
                                                                         dst_props=("path",))]
        kind_rows = self.g.query(f"MATCH (a:{API} {{id: $id}}) RETURN a.kind AS kind", {"id": aid})
        return ApiTimeline(library, path, kind_rows[0]["kind"] if kind_rows else "", versions, repl,
                           self.g.requests - q0)


class RecordingLearner:
    """Writes each episode as an Episode node + USED edges, and moves CUES weights behind the context it was given."""

    STEP = 0.25

    def __init__(self, graph: HydraGraph, ingestor=None, run_id: str = "live"):
        self.g, self.run_id = graph, run_id
        self.episodes: list[Episode] = []
        self.maintained = 0

    def record(self, episode: Episode) -> LearnReport:
        self.episodes.append(episode)
        key = f"{episode.run_id}|{episode.problem_id}|{episode.iteration}"
        eid = upsert_nodes(self.g, EPISODE, [{
            "key": key, "run_id": episode.run_id, "problem_id": episode.problem_id, "library": episode.library,
            "version": episode.version, "passed": episode.passed, "error_type": episode.error_type,
            "iteration": episode.iteration,
        }])[0]
        report = LearnReport()
        if episode.context is None:
            return report
        apis = [node_id(API, f"{episode.library}|{i.path}") for i in episode.context.items]
        link_props_many(self.g, USED, [(eid, a, {"helped": episode.passed}) for a in apis])
        words = sorted({w for w in re.findall(r"[a-z]+", episode.problem.lower())})
        tids = [node_id(TERM, f"{episode.library}:{w}") for w in words]
        rows = [r for r in edges_from(self.g, tids, CUES, props=("w",)) if r["dst"] in set(apis)] if tids else []
        delta = self.STEP if episode.passed else -self.STEP
        set_edges_many(self.g, CUES, [(r["src"], r["dst"], {"w": round((r["w"] or 0) + delta, 4)}) for r in rows])
        report.edges_reweighted = len(rows)
        return report

    def maintain(self) -> LearnReport:
        self.maintained += 1
        return LearnReport(notes=["test learner: nothing to compress"])


def build_services(settings):
    """PINNED_FACTORY entry for the live run: the stand-ins above over the configured HydraDB node."""
    g = HydraGraph(url=settings.hydra_url)
    return GraphBackedSearch(g), RecordingLearner(g), g
