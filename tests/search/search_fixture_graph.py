"""Fixture-graph builder shared by the search tests and the latency bench (tests only, never product code).
Writes exactly the contract.py schema with the pinned.graph batch helpers."""

from __future__ import annotations

from dataclasses import dataclass, field

from pinned.contract import (
    ADDED_IN,
    ALIAS_OF,
    API,
    CHANGED_IN,
    CUES,
    HAS_VERSION,
    LIBRARY,
    MEMBER_OF,
    NEXT,
    REMOVED_IN,
    REPLACED_BY,
    SHORTCUT,
    TERM,
    VERSION,
    W_DEFAULT,
)
from pinned.graph import link_many, link_props_many, node_id, upsert_nodes, version_ord


@dataclass
class LibBuilder:
    """Collects one fake library's facts, then writes them in batches (`commit`)."""

    g: object
    name: str
    imp: str
    versions: list[str]
    apis: dict[str, str] = field(default_factory=dict)
    added: list[tuple[int, int, dict]] = field(default_factory=list)
    removed: list[tuple[int, int, dict]] = field(default_factory=list)
    changed: list[tuple[int, int, dict]] = field(default_factory=list)
    replaced: list[tuple[int, int, dict]] = field(default_factory=list)
    aliases: list[tuple[int, int, dict]] = field(default_factory=list)
    members: list[tuple[int, int]] = field(default_factory=list)
    cues: list[tuple[int, int, dict]] = field(default_factory=list)
    shortcuts: list[tuple[int, int, dict]] = field(default_factory=list)
    words: set[str] = field(default_factory=set)

    def vid(self, version: str) -> int:
        return node_id(VERSION, f"{self.name}|{version}")

    def aid(self, path: str) -> int:
        return node_id(API, f"{self.name}|{self.p(path)}")

    def p(self, path: str) -> str:
        """Short path "f" or "sub.f" -> full import path "<imp>.sub.f"."""
        return path if path.startswith(self.imp + ".") or path == self.imp else f"{self.imp}.{path}"

    def api(self, path: str, kind: str = "function", added=(), removed=(), changed=()) -> LibBuilder:
        """added: [(version, sig)], removed: [version], changed: [(version, kind, before, after)]."""
        full = self.p(path)
        self.apis[full] = kind
        a = self.aid(full)
        for ver, sig in added:
            self.added.append((a, self.vid(ver), {"evidence": "fixture", "sig": sig}))
        for ver in removed:
            self.removed.append((a, self.vid(ver), {"evidence": "fixture"}))
        for ver, k, before, after in changed:
            self.changed.append((a, self.vid(ver), {"kind": k, "before": before, "after": after, "evidence": "fixture"}))
        return self

    def replace(self, old: str, new: str, w: float = W_DEFAULT, evidence: str = "use new instead", since: str = ""):
        props = {"w": w, "evidence": evidence, "since_ord": version_ord(since) if since else 0}
        self.replaced.append((self.aid(old), self.aid(new), props))
        return self

    def alias(self, a: str, b: str):
        self.aliases.append((self.aid(a), self.aid(b), {"evidence": "fixture"}))
        return self

    def member(self, child: str, parent: str):
        self.members.append((self.aid(child), self.aid(parent)))
        return self

    def cue(self, word: str, path: str, w: float = W_DEFAULT, source: str = "doc"):
        self.words.add(word)
        self.cues.append((node_id(TERM, word), self.aid(path), {"w": w, "source": source}))
        return self

    def shortcut(self, word: str, path: str, lo: str, hi: str, w: float = W_DEFAULT, hops: int = 2):
        self.words.add(word)
        self.shortcuts.append((node_id(TERM, word), self.aid(path),
                               {"w": w, "lo_ord": version_ord(lo), "hi_ord": version_ord(hi), "hops": hops}))
        return self

    def commit(self) -> LibBuilder:
        g = self.g
        lib = upsert_nodes(g, LIBRARY, [{"key": self.name, "name": self.name, "import_names": self.imp}])[0]
        vrows = [{"key": f"{self.name}|{v}", "library": self.name, "version": v, "ord": version_ord(v),
                  "released": "2020-01-01"} for v in self.versions]
        vids = upsert_nodes(g, VERSION, vrows)
        link_many(g, HAS_VERSION, [(lib, v) for v in vids])
        ordered = [i for _, i in sorted(zip([version_ord(v) for v in self.versions], vids))]
        link_many(g, NEXT, list(zip(ordered, ordered[1:])))
        upsert_nodes(g, API, [{"key": f"{self.name}|{p}", "library": self.name, "path": p, "kind": k}
                              for p, k in self.apis.items()])
        upsert_nodes(g, TERM, [{"key": w, "word": w} for w in sorted(self.words)])
        for rel, rows in ((ADDED_IN, self.added), (REMOVED_IN, self.removed), (CHANGED_IN, self.changed),
                          (REPLACED_BY, self.replaced), (ALIAS_OF, self.aliases), (CUES, self.cues),
                          (SHORTCUT, self.shortcuts)):
            if rows:
                link_props_many(g, rel, rows)
        if self.members:
            link_many(g, MEMBER_OF, self.members)
        return self
