"""Every graph read search makes. Few round trips per answer:

1. `library_meta` - Library + all its Versions (HAS_VERSION)                                          1 request
2. `cues`         - CUES out of the words that are not hubs UNION ALL SHORTCUT out of every word       1 request
3. `hub_cues`     - CUES from hub words INTO the candidates found so far (anchored on the Api side)    1 request
4. `events`       - ADDED_IN / REMOVED_IN / CHANGED_IN of many Apis with Version.ord, plus whether each
                    Api has REPLACED_BY / ALIAS_OF edges out or in                                     1 request
5. `walk_out` / `walk_in` - `algo.SSpaths` over REPLACED_BY / ALIAS_OF (+ the version events on the way),
                    only for seeds that have such edges                                                1 per seed

Why hubs are special (measured on HydraDB 0.4.0, docs/progress/C-search.md): expanding a node costs ~0.15 ms per
edge of the expanded type, and LIMIT does not cut it, so a word that cues 1,600 APIs costs ~240 ms. Reading the same
CUES edges from the Api side (few incoming edges each) costs ~2 ms. So per call only the rarest words (within an edge
budget) discover candidates; every other word is a hub whose (learned) weight still counts on the candidates found by
the rare words and by the code.
"""

from __future__ import annotations

import threading
from collections import defaultdict
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


OPEN_BUDGET = 250      # CUES edges (all libraries) search may expand per call to discover candidates: ~40 ms


class TermStats:
    """Process-wide memory of how many CUES edges each word has (all libraries, and per library).

    It is a cost hint and the idf denominator, never a source of answers: every weight search scores with is read live
    from the graph on each call. A word is learned the first time it is expanded; degrees only drift when learning
    adds a learned cue, which barely moves idf. Bounded: cleared when it grows past `max_terms`.
    """

    def __init__(self, max_terms: int = 200_000):
        self.max_terms = max_terms
        self._total: dict[int, int] = {}
        self._lib: dict[tuple[int, str], int] = {}
        self._lock = threading.Lock()

    def total(self, term_id: int) -> int | None:
        """CUES edges of the word in all libraries, None if never expanded by this process."""
        return self._total.get(term_id)

    def plan(self, term_ids: list[int], budget: int) -> tuple[list[int], list[int]]:
        """Split words into (expand, hubs). Never-seen words are expanded (that is how they get learned). Known words
        are expanded rarest first while their edges fit in `budget` (the rarest always is); the rest are hubs. Words
        known to have no CUES edges are dropped."""
        expand = [i for i in term_ids if i not in self._total]
        known = sorted((self._total[i], i) for i in term_ids if self._total.get(i))
        hubs: list[int] = []
        spent = 0
        for n, i in known:
            if spent == 0 or spent + n <= budget:
                expand.append(i)
                spent += n
            else:
                hubs.append(i)
        return expand, hubs

    def degree(self, term_id: int, library: str) -> int:
        return self._lib.get((term_id, library), 0)

    def learn(self, term_id: int, total: int, per_lib: dict[str, int]) -> None:
        with self._lock:
            if len(self._total) >= self.max_terms:
                self._total.clear()
                self._lib.clear()
            self._total[term_id] = total
            for lib, n in per_lib.items():
                self._lib[(term_id, lib)] = n


def _cue(by_id: dict[int, str], r: dict) -> Cue:
    source = str(r.get("extra") or "") if r["rel"] == CUES else ""
    return Cue(by_id[r["t"]], r["a"], str(r["path"]), float(r["w"]), r["rel"], source)


def cues(g: CountingGraph, library: str, terms: list[str], ord_v: int, stats: TermStats) -> tuple[list[Cue], list[str]]:
    """CUES (w >= W_FLOOR) out of the words `stats.plan` picks for expansion and SHORTCUT edges (w >= W_FLOOR, lo_ord <= ord_v <= hi_ord) out of
    every word, into this library, in one request. Returns (cues, hub words). Updates `stats` for expanded words."""
    by_id = {node_id(TERM, t): t for t in terms}
    open_ids, hub_ids = stats.plan(list(by_id), OPEN_BUDGET)
    hubs = [by_id[i] for i in hub_ids]
    q = (
        f"MATCH (t)-[r:{CUES}]->(a) WHERE t.id IN $open "
        f"RETURN t.id AS t, a.id AS a, a.path AS path, a.library AS lib, r.w AS w, r.source AS extra, '{CUES}' AS rel "
        "UNION ALL "
        f"MATCH (t)-[r:{SHORTCUT}]->(a) WHERE t.id IN $all AND a.library = $lib AND r.w >= $floor "
        "AND r.lo_ord <= $o AND r.hi_ord >= $o "
        f"RETURN t.id AS t, a.id AS a, a.path AS path, a.library AS lib, r.w AS w, r.hops AS extra, '{SHORTCUT}' AS rel"
    )
    rows = g.query(q, {"open": open_ids, "all": list(by_id), "lib": library, "floor": W_FLOOR, "o": ord_v}) if by_id else []
    total: dict[int, int] = defaultdict(int)
    per_lib: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    out: list[Cue] = []
    for r in rows:
        if r["rel"] == CUES:
            total[r["t"]] += 1
            ok = r.get("w") is not None and float(r["w"]) >= W_FLOOR
            if ok and r.get("lib") is not None:
                per_lib[r["t"]][str(r["lib"])] += 1
            if not ok or r.get("lib") != library or r.get("path") is None:
                continue
        out.append(_cue(by_id, r))
    for i in open_ids:
        stats.learn(i, total.get(i, 0), dict(per_lib.get(i, {})))
    return out, hubs


def hub_cues(g: CountingGraph, hubs: list[str], api_ids: list[int]) -> list[Cue]:
    """CUES (w >= W_FLOOR) from hub words into the given Apis, read from the Api side (cheap: few incoming edges)."""
    if not hubs or not api_ids:
        return []
    by_id = {node_id(TERM, t): t for t in hubs}
    # the a.id condition must come first: HydraDB anchors on the first IN (t first = expand the hub: 20x slower)
    q = (f"MATCH (t)-[r:{CUES}]->(a) WHERE a.id IN $apis AND t.id IN $terms AND r.w >= $floor "
         f"RETURN t.id AS t, a.id AS a, a.path AS path, r.w AS w, r.source AS extra, '{CUES}' AS rel")
    out: list[Cue] = []
    ids = list(dict.fromkeys(api_ids))
    for i in range(0, len(ids), CHUNK):
        for r in g.query(q, {"apis": ids[i : i + CHUNK], "terms": list(by_id), "floor": W_FLOOR}):
            if r.get("path") is not None and r.get("w") is not None:
                out.append(_cue(by_id, r))
    return out


@dataclass
class ApiFacts:
    id: int
    path: str = ""
    kind: str = ""
    events: list[Event] = field(default_factory=list)
    chains_out: bool = False   # has REPLACED_BY / ALIAS_OF edges out (worth an outgoing walk)
    chains_in: bool = False    # has REPLACED_BY / ALIAS_OF edges in (worth an incoming walk)


def _event(rel: str, r: dict) -> Event:
    return Event(rel=rel, ord=int(r["ord"]), version=str(r.get("version") or ""), sig=str(r.get("sig") or ""),
                 kind=str(r.get("kind") or ""), before=str(r.get("before") or ""), after=str(r.get("after") or ""),
                 evidence=str(r.get("evidence") or ""))


def events(g: CountingGraph, api_ids: list[int]) -> dict[int, ApiFacts]:
    """Version events of many Apis, and whether each has REPLACED_BY / ALIAS_OF edges out or in: ONE request per 1000
    ids. Apis with neither events nor chain edges are absent."""
    cols = ("a.path AS path, a.kind AS akind, '{rel}' AS rel, v.ord AS ord, v.version AS version, r.sig AS sig, "
            "r.kind AS kind, r.before AS before, r.after AS after, r.evidence AS evidence")
    branches = [f"MATCH (a)-[r:{rel}]->(v) WHERE a.id IN $ids RETURN a.id AS a, " + cols.format(rel=rel)
                for rel in EVENT_RELS]
    for rel in CHAIN_RELS:  # chain edges out (tag OUT) and in (tag IN); v is the other Api, its ord is null
        branches.append(f"MATCH (a)-[r:{rel}]->(v) WHERE a.id IN $ids RETURN a.id AS a, " + cols.format(rel="OUT"))
        branches.append(f"MATCH (v)-[r:{rel}]->(a) WHERE a.id IN $ids RETURN a.id AS a, " + cols.format(rel="IN"))
    q = " UNION ALL ".join(branches)
    out: dict[int, ApiFacts] = {}
    ids = list(dict.fromkeys(api_ids))
    for i in range(0, len(ids), CHUNK):
        for r in g.query(q, {"ids": ids[i : i + CHUNK]}):
            f = out.setdefault(r["a"], ApiFacts(r["a"], str(r.get("path") or ""), str(r.get("akind") or "")))
            if r["rel"] == "OUT":
                f.chains_out = True
            elif r["rel"] == "IN":
                f.chains_in = True
            elif r.get("ord") is not None:
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
