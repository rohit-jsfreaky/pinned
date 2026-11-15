"""What the graph says about APIs at a version: the contract.py validity rule, signature rule and deprecation rule.

Pure functions over an `ApiFacts` record, plus a loader for the few APIs an episode names. Used by
the learner (gap checks, entity split) and by PlainStore (which keeps the same events in plain dicts).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pinned.contract import ADDED_IN, CHANGED_IN, REMOVED_IN, Status
from pinned.contract import Graph as GraphProto


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
    """ADDED_IN / REMOVED_IN / CHANGED_IN of the given Api nodes (three small index-anchored requests per API; the
    learner only asks about the handful an episode names). APIs with no edges get an empty record."""
    out: dict[int, ApiFacts] = {i: ApiFacts() for i in ids}
    if not ids:
        return out
    for aid in dict.fromkeys(ids):
        _load_one(g, aid, out[aid])
    return {i: f.sort() for i, f in out.items()}


def _load_one(g: GraphProto, aid: int, f: ApiFacts) -> None:
    """Three map-anchored reads (an `a.id IN $list` edge read is a full edge scan on HydraDB 0.4.0; see reads.py)."""
    for row in g.query(f"MATCH (a {{id: $id}})-[r:{ADDED_IN}]->(v) RETURN v.ord AS ord, r.sig AS sig", {"id": aid}):
        if row["ord"] is not None:
            f.events.append((int(row["ord"]), ADDED_IN, row["sig"] or ""))
    for row in g.query(f"MATCH (a {{id: $id}})-[r:{REMOVED_IN}]->(v) RETURN v.ord AS ord", {"id": aid}):
        if row["ord"] is not None:
            f.events.append((int(row["ord"]), REMOVED_IN, ""))
    for row in g.query(f"MATCH (a {{id: $id}})-[r:{CHANGED_IN}]->(v) RETURN v.ord AS ord, r.kind AS kind, "
                       "r.after AS after", {"id": aid}):
        if row["ord"] is not None:
            f.changes.append((int(row["ord"]), row["kind"] or "", row["after"] or ""))
