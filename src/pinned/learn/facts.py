"""What the graph says about APIs at a version: the contract.py validity rule, signature rule and deprecation rule.

Pure functions over an `ApiFacts` record, plus one batched loader (three requests for any number of APIs). Used by
the learner (gap checks, entity split) and by PlainStore (which keeps the same events in plain dicts).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pinned.contract import ADDED_IN, CHANGED_IN, REMOVED_IN, Status
from pinned.contract import Graph as GraphProto

CHUNK = 1000


@dataclass
class ApiFacts:
    events: list[tuple[int, str, str]] = field(default_factory=list)   # (ord, ADDED_IN|REMOVED_IN, sig)
    changes: list[tuple[int, str, str]] = field(default_factory=list)  # (ord, kind, after)

    def sort(self) -> ApiFacts:
        self.events.sort()
        self.changes.sort()
        return self

    def status_at(self, ord_: int) -> Status:
        past = [e for e in self.events if e[0] <= ord_]
        if not past:
            return "added_later" if any(e[1] == ADDED_IN for e in self.events) else "missing"
        latest = max(past, key=lambda e: (e[0], e[1] == REMOVED_IN))
        if latest[1] == REMOVED_IN:
            return "removed"
        if any(c[0] <= ord_ and c[1] == "deprecated" for c in self.changes):
            return "deprecated"
        return "valid"

    def signature_at(self, ord_: int) -> str:
        sig_changes = [c for c in self.changes if c[0] <= ord_ and c[1] == "signature" and c[2]]
        if sig_changes:
            return max(sig_changes)[2]
        adds = [e for e in self.events if e[0] <= ord_ and e[1] == ADDED_IN]
        return max(adds)[2] if adds else ""

    def deprecated_at(self, ord_: int) -> bool:
        return any(c[0] <= ord_ and c[1] == "deprecated" for c in self.changes)

    def has_change(self, lo_exclusive: int, hi_inclusive: int, kind: str) -> bool:
        return any(lo_exclusive < c[0] <= hi_inclusive and c[1] == kind for c in self.changes)


def usable(status: Status) -> bool:
    return status in ("valid", "deprecated")


def has_param(signature: str, name: str) -> bool:
    """True if `name` is a parameter in a signature string like "(a, b=1, *, normalize: bool = False)"."""
    return re.search(rf"(?<![\w*.]){re.escape(name)}\s*(?:[=:,)]|$)", signature) is not None


def load_facts(g: GraphProto, ids: list[int]) -> dict[int, ApiFacts]:
    """ADDED_IN / REMOVED_IN / CHANGED_IN of many Api nodes in three requests. APIs with no edges get an empty record."""
    out: dict[int, ApiFacts] = {i: ApiFacts() for i in ids}
    if not ids:
        return out
    unique = list(dict.fromkeys(ids))
    for i in range(0, len(unique), CHUNK):
        _load_chunk(g, unique[i : i + CHUNK], out)
    return {i: f.sort() for i, f in out.items()}


def _load_chunk(g: GraphProto, ids: list[int], out: dict[int, ApiFacts]) -> None:
    rows = g.query(f"MATCH (a)-[r:{ADDED_IN}]->(v) WHERE a.id IN $ids RETURN a.id AS id, v.ord AS ord, r.sig AS sig",
                   {"ids": ids})
    for row in rows:
        if row["ord"] is not None:
            out[row["id"]].events.append((int(row["ord"]), ADDED_IN, row["sig"] or ""))
    rows = g.query(f"MATCH (a)-[r:{REMOVED_IN}]->(v) WHERE a.id IN $ids RETURN a.id AS id, v.ord AS ord", {"ids": ids})
    for row in rows:
        if row["ord"] is not None:
            out[row["id"]].events.append((int(row["ord"]), REMOVED_IN, ""))
    rows = g.query(
        f"MATCH (a)-[r:{CHANGED_IN}]->(v) WHERE a.id IN $ids RETURN a.id AS id, v.ord AS ord, r.kind AS kind, r.after AS after",
        {"ids": ids},
    )
    for row in rows:
        if row["ord"] is not None:
            out[row["id"]].changes.append((int(row["ord"]), row["kind"] or "", row["after"] or ""))
