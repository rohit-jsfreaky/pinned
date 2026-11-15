"""Pick the replacement for an API that is not usable at the asked version, and turn walks into evidence Hops."""

from __future__ import annotations

from dataclasses import dataclass

from pinned.contract import ADDED_IN, ALIAS_OF, CHANGED_IN, REMOVED_IN, REPLACED_BY, W_DEFAULT, W_FLOOR, Hop
from pinned.graph import Path

from .facts import ApiFacts, LibraryMeta, Walk
from .validity import Verdict, verdict_at

HOP_DECAY = 0.5        # each extra hop halves a chain's strength, so the nearest valid API wins unless learning says so
DEPRECATED_PENALTY = 0.5


@dataclass
class Replacement:
    api: int
    path: str
    verdict: Verdict
    hops: list[Hop]        # REPLACED_BY / ALIAS_OF hops, true edge direction
    strength: float
    reverse: bool = False  # found by walking INTO the seed (an older API that did the job)


def _rel_w(rel: dict) -> float:
    if rel["type"] != REPLACED_BY:
        return 1.0
    w = rel.get("w")
    return float(w) if w is not None else W_DEFAULT


def chain_hops(p: Path) -> list[Hop]:
    """Path -> hops with the true direction of every edge (incoming walks list nodes from the seed backwards)."""
    paths = {n["id"]: str(n.get("path") or n.get("key") or n["id"]) for n in p.nodes}
    out = []
    for r in p.rels:
        note = ""
        if r["type"] == REPLACED_BY:
            note = f"w={_rel_w(r):.2f}"
            if r.get("evidence"):
                note += f"; {r['evidence']}"
        elif r.get("evidence"):
            note = str(r["evidence"])
        out.append(Hop(paths.get(r["src"], str(r["src"])), r["type"], paths.get(r["dst"], str(r["dst"])), note))
    return out


def best_replacement(walks: list[Walk], facts: dict[int, ApiFacts], ord_v: int) -> Replacement | None:
    """Among the APIs reachable from the seed, the strongest one usable at ord_v.

    strength = product of REPLACED_BY weights x HOP_DECAY^(hops-1) (x DEPRECATED_PENALTY if deprecated there).
    Chains through a REPLACED_BY edge with w < W_FLOOR are ignored (learning decayed them). Ties: fewer hops, then path.
    """
    best: tuple[tuple, Replacement] | None = None
    for walk in walks:
        for p in walk.chains:
            end = p.nodes[-1]
            if end["id"] == walk.seed or any(r["type"] not in (REPLACED_BY, ALIAS_OF) for r in p.rels):
                continue
            weights = [_rel_w(r) for r in p.rels]
            if any(w < W_FLOOR for w in weights):
                continue
            f = facts.get(end["id"])
            if f is None:
                continue
            v = verdict_at(f.events, ord_v)
            if not v.usable:
                continue
            strength = HOP_DECAY ** (len(p.rels) - 1)
            for w in weights:
                strength *= w
            if v.status == "deprecated":
                strength *= DEPRECATED_PENALTY
            path = f.path or str(end.get("path") or "")
            key = (-strength, len(p.rels), path)
            if best is None or key < best[0]:
                best = (key, Replacement(end["id"], path, v, chain_hops(p), strength, walk.direction == "incoming"))
    return best[1] if best else None


def version_hop(meta: LibraryMeta, path: str, verdict: Verdict, version: str) -> list[Hop]:
    """The version facts that explain a verdict: when it was added / removed, deprecated, or that it comes later."""
    hops: list[Hop] = []
    d = verdict.deciding
    if d is not None:
        if verdict.status == "added_later":
            hops.append(Hop(path, ADDED_IN, meta.label(d.version), f"after {version}"))
        elif d.rel == REMOVED_IN:
            hops.append(Hop(path, REMOVED_IN, meta.label(d.version), d.evidence))
        else:
            hops.append(Hop(path, ADDED_IN, meta.label(d.version), ""))
    if verdict.deprecation is not None:
        e = verdict.deprecation
        hops.append(Hop(path, CHANGED_IN, meta.label(e.version), f"deprecated: {e.after or e.evidence}".rstrip(": ")))
    return hops


def is_rename(seed_path: str, rep: Replacement) -> bool:
    """A removed API whose replacement is the same object under a new name/module: an ALIAS_OF first hop, the same
    last name in another module, or REPLACED_BY evidence that says renamed / moved."""
    if not rep.hops or rep.reverse:
        return False
    first = rep.hops[0]
    if first.rel == ALIAS_OF:
        return True
    if seed_path.rsplit(".", 1)[-1] == rep.path.rsplit(".", 1)[-1]:
        return True
    text = first.note.lower()
    return "renam" in text or "moved" in text

