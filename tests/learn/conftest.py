"""Fixture graph for the learner tests, built with pinned.graph helpers in exactly the contract.py schema.

Library `lib<k>` (import name `m<k>`), versions 1.0 -> 2.0 -> 3.0. Every key carries the test's run_key, so tests
never share Api nodes; Term words carry it too (`bessel<k>`), so learned edges never collide either.

    m.old_fn   ADDED 1.0 (x)  REMOVED 2.0   -REPLACED_BY{w=1}-> m.mid_fn
    m.mid_fn   ADDED 1.0 (x)  REMOVED 3.0   -ALIAS_OF->        m.sub.new_fn
    m.sub.new_fn ADDED 2.0 (x, axis=0)
    m.Frame (class) ADDED 1.0 (data);  m.Frame.append ADDED 1.0 (self, other) REMOVED 2.0 -REPLACED_BY-> m.concat
    m.concat   ADDED 1.0 (objs, axis=0)
    m.fit      ADDED 1.0 (x, normalize=False)        (graph never learned that 2.0 dropped `normalize`)
    m.helper / m.tools.helper  same sig (a, b=2) at every version   (re-export)
    m.p.solve / m.q.solve      same sig (a), same doc line "Solve it."
    m.x.compute / m.y.compute  same sig (a), no doc                 (must NOT merge)
    m.legacy   ADDED 1.0 (a)                                         (graph has no deprecation fact)
Terms: bessel<k> -CUES{w=1, doc}-> m.old_fn ; frame<k> -> m.Frame.append ; concat<k> -> m.concat ; fit<k> -> m.fit
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from pinned.contract import (
    ADDED_IN,
    ALIAS_OF,
    API,
    CUES,
    HAS_VERSION,
    LIBRARY,
    NEXT,
    REMOVED_IN,
    REPLACED_BY,
    TERM,
    VERSION,
    Context,
    ContextItem,
    Episode,
    Hop,
    IngestReport,
)
from pinned.graph import link_many, link_props_many, node_id, upsert_nodes, version_ord

VERSIONS = ("1.0", "2.0", "3.0")


@dataclass
class World:
    graph: object
    k: str
    lib: str
    mod: str
    run_id: str
    words: dict[str, str]
    ids: dict[str, int] = field(default_factory=dict)

    def p(self, short: str) -> str:
        """`old_fn` -> `m<k>.old_fn` (dotted import path)."""
        return f"{self.mod}.{short}"

    def api(self, short: str) -> int:
        return node_id(API, f"{self.lib}|{self.p(short)}")

    def term(self, name: str) -> int:
        return node_id(TERM, self.words[name])

    def ver(self, v: str) -> int:
        return node_id(VERSION, f"{self.lib}|{v}")

    def edge(self, rel: str, src: int, dst: int, props=("w", "n_pass", "n_fail", "source")) -> dict | None:
        cols = ", ".join(f"r.{p} AS {p}" for p in props)
        rows = self.graph.query(f"MATCH (a {{id: $s}})-[r:{rel}]->(b {{id: $d}}) RETURN {cols}", {"s": src, "d": dst})
        return rows[0] if rows else None

    def episode(self, pid: str, version: str, code: str, passed: bool, problem: str = "", context: Context | None = None,
                error_type: str = "", error_text: str = "", iteration: int = 1) -> Episode:
        return Episode(run_id=self.run_id, problem_id=pid, iteration=iteration, library=self.lib, version=version,
                       problem=problem, starting_code="", context=context, code=code, passed=passed,
                       error_type=error_type, error_text=error_text)

    def chain_ctx(self, version: str) -> Context:
        """What search returns for `bessel` at 2.0/3.0: old_fn is gone, walk REPLACED_BY + ALIAS_OF to new_fn."""
        chain = [
            Hop(self.p("old_fn"), REMOVED_IN, f"{self.lib} 2.0"),
            Hop(self.p("old_fn"), REPLACED_BY, self.p("mid_fn")),
            Hop(self.p("mid_fn"), ALIAS_OF, self.p("sub.new_fn")),
        ]
        return Context(self.lib, version, [ContextItem(self.lib, self.p("sub.new_fn"), "valid", chain, "(x, axis=0)")])


def build_world(g, k: str) -> World:
    lib, mod = f"lib{k}", f"m{k}"
    w = World(g, k, lib, mod, f"run{k}", {n: f"{n}{k}" for n in ("bessel", "frame", "concat", "fit")})
    upsert_nodes(g, LIBRARY, [{"key": lib, "name": lib, "import_names": mod}])
    upsert_nodes(g, VERSION, [{"key": f"{lib}|{v}", "library": lib, "version": v, "ord": version_ord(v),
                               "released": f"202{i}-01-01"} for i, v in enumerate(VERSIONS)])
    link_many(g, HAS_VERSION, [(node_id(LIBRARY, lib), w.ver(v)) for v in VERSIONS])
    link_many(g, NEXT, [(w.ver(a), w.ver(b)) for a, b in zip(VERSIONS, VERSIONS[1:])])
    apis = {
        "old_fn": "function", "mid_fn": "function", "sub.new_fn": "function", "Frame": "class",
        "Frame.append": "method", "concat": "function", "fit": "function", "helper": "function",
        "tools.helper": "function", "p.solve": "function", "q.solve": "function", "x.compute": "function",
        "y.compute": "function", "legacy": "function",
    }
    docs = {"p.solve": "Solve it.", "q.solve": "Solve it."}
    upsert_nodes(g, API, [{"key": f"{lib}|{w.p(s)}", "library": lib, "path": w.p(s), "kind": kind, "doc": docs.get(s)}
                          for s, kind in apis.items()])
    added = [("old_fn", "1.0", "(x)"), ("mid_fn", "1.0", "(x)"), ("sub.new_fn", "2.0", "(x, axis=0)"),
             ("Frame", "1.0", "(data)"), ("Frame.append", "1.0", "(self, other)"), ("concat", "1.0", "(objs, axis=0)"),
             ("fit", "1.0", "(x, normalize=False)"), ("helper", "1.0", "(a, b=2)"), ("tools.helper", "1.0", "(a, b=2)"),
             ("p.solve", "1.0", "(a)"), ("q.solve", "1.0", "(a)"), ("x.compute", "1.0", "(a)"),
             ("y.compute", "1.0", "(a)"), ("legacy", "1.0", "(a)")]
    link_props_many(g, ADDED_IN, [(w.api(s), w.ver(v), {"sig": sig, "evidence": "fixture"}) for s, v, sig in added])
    link_props_many(g, REMOVED_IN, [(w.api(s), w.ver(v), {"evidence": "fixture"})
                                    for s, v in (("old_fn", "2.0"), ("mid_fn", "3.0"), ("Frame.append", "2.0"))])
    link_props_many(g, REPLACED_BY, [
        (w.api("old_fn"), w.api("mid_fn"), {"w": 1.0, "evidence": "fixture", "since_ord": version_ord("2.0")}),
        (w.api("Frame.append"), w.api("concat"), {"w": 1.0, "evidence": "fixture", "since_ord": version_ord("2.0")}),
    ])
    link_props_many(g, ALIAS_OF, [(w.api("mid_fn"), w.api("sub.new_fn"), {"evidence": "fixture"})])
    upsert_nodes(g, TERM, [{"key": word, "word": word} for word in w.words.values()])
    link_props_many(g, CUES, [
        (w.term("bessel"), w.api("old_fn"), {"w": 1.0, "source": "doc"}),
        (w.term("frame"), w.api("Frame.append"), {"w": 1.0, "source": "name"}),
        (w.term("concat"), w.api("concat"), {"w": 1.0, "source": "name"}),
        (w.term("fit"), w.api("fit"), {"w": 1.0, "source": "name"}),
    ])
    return w


@pytest.fixture()
def world(graph, run_key) -> World:
    return build_world(graph, run_key)


class FakeIngestor:
    """Records every fill_gap call; optionally raises to test error handling."""

    def __init__(self, fail: bool = False):
        self.calls: list[tuple[str, str, str]] = []
        self.fail = fail

    def ingest_version(self, library: str, version: str) -> IngestReport:
        return IngestReport(library, version)

    def fill_gap(self, library: str, version: str, hint: str) -> IngestReport:
        self.calls.append((library, version, hint))
        if self.fail:
            raise RuntimeError("PyPI unreachable")
        return IngestReport(library, version, apis=1, added=1)


@pytest.fixture()
def ingestor() -> FakeIngestor:
    return FakeIngestor()
