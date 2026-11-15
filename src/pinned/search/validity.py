"""The validity rule from contract.py, as pure functions over an API's version events.

An Api is valid at version v <=> among its ADDED_IN / REMOVED_IN events with ord <= ord(v), the latest is ADDED_IN.
No ADDED_IN at or before v -> "added_later" if a later ADDED_IN exists, else "missing". A CHANGED_IN{kind=deprecated}
at or before v (and after the latest ADDED_IN) -> "deprecated" (still usable).
Signature at v: `after` of the latest CHANGED_IN{kind=signature} with ord <= v, else `sig` of the latest ADDED_IN <= v.
"""

from __future__ import annotations

from dataclasses import dataclass

from pinned.contract import ADDED_IN, CHANGED_IN, REMOVED_IN, Status


@dataclass(frozen=True)
class Event:
    """One ADDED_IN / REMOVED_IN / CHANGED_IN edge of an Api, with its Version's ord and version string."""

    rel: str
    ord: int
    version: str
    sig: str = ""
    kind: str = ""
    before: str = ""
    after: str = ""
    evidence: str = ""


@dataclass(frozen=True)
class Verdict:
    """Status of one Api at one version, plus the event that decided it (for the evidence chain)."""

    status: Status
    signature: str = ""
    deciding: Event | None = None      # latest ADDED_IN/REMOVED_IN <= v, or the first later ADDED_IN (added_later)
    deprecation: Event | None = None   # CHANGED_IN{kind=deprecated} that makes it deprecated

    @property
    def usable(self) -> bool:
        return self.status in ("valid", "deprecated")


# At equal ord an ADDED_IN sorts after REMOVED_IN, so "removed then re-added in the same release" reads as valid.
_REL_ORDER = {REMOVED_IN: 0, ADDED_IN: 1}


def _life(events: list[Event]) -> list[Event]:
    return sorted((e for e in events if e.rel in _REL_ORDER), key=lambda e: (e.ord, _REL_ORDER[e.rel]))


def verdict_at(events: list[Event], ord_v: int) -> Verdict:
    """Apply the contract's validity rule at version ord `ord_v`."""
    life = _life(events)
    past = [e for e in life if e.ord <= ord_v]
    if not past:
        later = next((e for e in life if e.rel == ADDED_IN), None)
        return Verdict("added_later", deciding=later) if later else Verdict("missing")
    last = past[-1]
    if last.rel == REMOVED_IN:
        return Verdict("removed", deciding=last)
    signature = signature_at(events, ord_v)
    deprecations = [e for e in events if e.rel == CHANGED_IN and e.kind == "deprecated" and last.ord <= e.ord <= ord_v]
    if deprecations:
        return Verdict("deprecated", signature, last, max(deprecations, key=lambda e: e.ord))
    return Verdict("valid", signature, last)


def signature_at(events: list[Event], ord_v: int) -> str:
    """`after` of the latest CHANGED_IN{kind=signature} <= v, else `sig` of the latest ADDED_IN <= v."""
    changes = [e for e in events if e.rel == CHANGED_IN and e.kind == "signature" and e.ord <= ord_v]
    if changes:
        return max(changes, key=lambda e: e.ord).after
    adds = [e for e in events if e.rel == ADDED_IN and e.ord <= ord_v]
    return max(adds, key=lambda e: e.ord).sig if adds else ""
