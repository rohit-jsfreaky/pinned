"""Every graph read search makes. Few round trips per answer:

1. `library_meta`  - Library + all its Versions (HAS_VERSION)                       1 request
2. `cues`          - CUES (w >= W_FLOOR) UNION ALL SHORTCUT (w >= W_FLOOR, ord in range) of many terms     1 request
3. `events`        - ADDED_IN UNION ALL REMOVED_IN UNION ALL CHANGED_IN of many Apis, with Version.ord    1 request
4. `walk_out`/`walk_in` - `algo.SSpaths` over REPLACED_BY / ALIAS_OF (+ the version events on the way)  1 per seed
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from pinned.contract import (
    ADDED_IN,
    ALIAS_OF,
    API,
    CHANGED_IN,
    CUES,
    HAS_VERSION,
    LIBRARY,
    REMOVED_IN,
    REPLACED_BY,
    SHORTCUT,
    TERM,
    W_FLOOR,
    Graph,
)
from pinned.graph import Path, node, node_id, reach
from pinned.graph.client import PAGE_SIZE

from .validity import Event

EVENT_RELS = (ADDED_IN, REMOVED_IN, CHANGED_IN)
CHAIN_RELS = (REPLACED_BY, ALIAS_OF)
CHUNK = 1000


class CountingGraph:
    """Wraps a Graph for ONE search call and counts its round trips (a paged result costs one per page).

    The shared client's `.requests` counter is exact only when nothing else uses the client at the same time; this
    per-call count stays exact under concurrency (tests check both agree at concurrency 1).
    """

    def __init__(self, graph: Graph):
        self.graph = graph
        self.requests = 0
        self._lock = threading.Lock()

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        rows = self.graph.query(cypher, params)
        with self._lock:
            self.requests += 1 + len(rows) // PAGE_SIZE
        return rows

    def write(self, cypher: str, params: dict | None = None) -> None:  # search never writes; here for the protocol
        raise RuntimeError("search is read-only")


@dataclass(frozen=True)
class VersionRow:
    version: str
    ord: int


@dataclass
class LibraryMeta:
    name: str
    import_names: set[str]
    versions: list[VersionRow]     # oldest -> newest

    def find(self, version: str, ord_v: int) -> VersionRow | None:
        exact = next((v for v in self.versions if v.version == version), None)
        return exact or next((v for v in self.versions if v.ord == ord_v), None)

    def label(self, version: str) -> str:
        return f"{self.name} {version}"


def library_meta(g: CountingGraph, library: str) -> LibraryMeta | None:
    lib_id = node_id(LIBRARY, library)
    rows = g.query(
        f"MATCH (l:{LIBRARY} {{id: $l}})-[:{HAS_VERSION}]->(v) RETURN l.import_names AS imp, v.version AS version, "
        "v.ord AS ord",
        {"l": lib_id},
    )
    if not rows:
        lib = node(g, LIBRARY, lib_id, ("import_names",))  # a library with no versions yet
        if lib is None:
            return None
        rows = [{"imp": lib.get("import_names"), "version": None, "ord": None}]
    imports = {s.strip() for s in str(rows[0].get("imp") or "").split(",") if s.strip()}
    if not imports:
        imports = {library.replace("-", "_")}
    versions = sorted({VersionRow(r["version"], int(r["ord"])) for r in rows if r.get("version") and r.get("ord") is not None},
                      key=lambda v: v.ord)
    return LibraryMeta(library, imports, versions)


@dataclass(frozen=True)
class Cue:
    term: str          # the Term word
    api: int
    path: str
    w: float
    rel: str           # CUES | SHORTCUT
    source: str = ""   # CUES.source (name|doc|learned)


def cues(g: CountingGraph, library: str, terms: list[str], ord_v: int) -> list[Cue]:
    """CUES edges with w >= W_FLOOR and SHORTCUT edges whose range holds ord_v, from the given words into this library."""
    by_id = {node_id(TERM, t): t for t in terms}
    q = (
        f"MATCH (t)-[r:{CUES}]->(a) WHERE t.id IN $ids AND a.library = $lib AND r.w >= $floor "
        f"RETURN t.id AS t, a.id AS a, a.path AS path, r.w AS w, r.source AS extra, '{CUES}' AS rel "
        "UNION ALL "
        f"MATCH (t)-[r:{SHORTCUT}]->(a) WHERE t.id IN $ids AND a.library = $lib AND r.w >= $floor "
        "AND r.lo_ord <= $o AND r.hi_ord >= $o "
        f"RETURN t.id AS t, a.id AS a, a.path AS path, r.w AS w, r.hops AS extra, '{SHORTCUT}' AS rel"
    )
    out: list[Cue] = []
    ids = list(by_id)
    for i in range(0, len(ids), CHUNK):
        for r in g.query(q, {"ids": ids[i : i + CHUNK], "lib": library, "floor": W_FLOOR, "o": ord_v}):
            if r.get("path") is None or r.get("w") is None:
                continue
            source = str(r.get("extra") or "") if r["rel"] == CUES else ""
            out.append(Cue(by_id[r["t"]], r["a"], r["path"], float(r["w"]), r["rel"], source))
    return out


@dataclass
class ApiFacts:
    id: int
    path: str = ""
    kind: str = ""
    events: list[Event] = field(default_factory=list)


def _event(rel: str, r: dict) -> Event:
    return Event(rel=rel, ord=int(r["ord"]), version=str(r.get("version") or ""), sig=str(r.get("sig") or ""),
                 kind=str(r.get("kind") or ""), before=str(r.get("before") or ""), after=str(r.get("after") or ""),
                 evidence=str(r.get("evidence") or ""))


def events(g: CountingGraph, api_ids: list[int]) -> dict[int, ApiFacts]:
    """Version events of many Apis in ONE request per 1000 ids. Apis without any event are absent."""
    branch = (
        "MATCH (a)-[r:{rel}]->(v) WHERE a.id IN $ids RETURN a.id AS a, a.path AS path, a.kind AS akind, "
        "'{rel}' AS rel, v.ord AS ord, v.version AS version, r.sig AS sig, r.kind AS kind, r.before AS before, "
        "r.after AS after, r.evidence AS evidence"
    )
    q = " UNION ALL ".join(branch.format(rel=rel) for rel in EVENT_RELS)
    out: dict[int, ApiFacts] = {}
    ids = list(dict.fromkeys(api_ids))
    for i in range(0, len(ids), CHUNK):
        for r in g.query(q, {"ids": ids[i : i + CHUNK]}):
            if r.get("ord") is None:
                continue
            f = out.setdefault(r["a"], ApiFacts(r["a"], str(r.get("path") or ""), str(r.get("akind") or "")))
            f.events.append(_event(r["rel"], r))
    for f in out.values():
        f.events.sort(key=lambda e: (e.ord, e.rel))
    return out


@dataclass
class Walk:
    """Everything reachable from one seed over REPLACED_BY / ALIAS_OF (max `MAX_HOPS` hops)."""

    seed: int
    chains: list[Path] = field(default_factory=list)     # paths of chain relationships only (seed -> ... -> api)
    facts: dict[int, ApiFacts] = field(default_factory=dict)   # events of every Api on those chains
    direction: str = "outgoing"


MAX_HOPS = 3
PATH_LIMIT = 2000


def walk_out(g: CountingGraph, seed: int) -> Walk:
    """ONE `algo.SSpaths` request: replacement chains out of `seed` AND the version events of every node on them
    (a path that ends with ADDED_IN/REMOVED_IN/CHANGED_IN carries that event of its last Api)."""
    w = Walk(seed)
    for p in reach(g, seed, CHAIN_RELS + EVENT_RELS, max_len=MAX_HOPS + 1, limit=PATH_LIMIT):
        if not p.rels:
            continue
        last = p.rels[-1]
        chain_part = p.rels if last["type"] in CHAIN_RELS else p.rels[:-1]
        if last["type"] in CHAIN_RELS:
            w.chains.append(p)
            continue
        if any(r["type"] not in CHAIN_RELS for r in chain_part):
            continue
        api, ver = p.nodes[-2], p.nodes[-1]
        if ver.get("ord") is None:
            continue
        f = w.facts.setdefault(api["id"], ApiFacts(api["id"], str(api.get("path") or ""), str(api.get("kind") or "")))
        e = _event(last["type"], {**last, "ord": ver["ord"], "version": ver.get("version")})
        if e not in f.events:
            f.events.append(e)
    for f in w.facts.values():
        f.events.sort(key=lambda e: (e.ord, e.rel))
    return w


def walk_in(g: CountingGraph, seed: int) -> Walk:
    """Chains INTO `seed` (who was replaced by / is an alias of it): for an API that is newer than the asked version,
    the older API that did the job. Events of those nodes come from a separate batched `events` call."""
    w = Walk(seed, direction="incoming")
    w.chains = [p for p in reach(g, seed, CHAIN_RELS, max_len=MAX_HOPS, limit=PATH_LIMIT, direction="incoming")
                if p.rels]
    return w


def api_id(library: str, path: str) -> int:
    return node_id(API, f"{library}|{path}")
