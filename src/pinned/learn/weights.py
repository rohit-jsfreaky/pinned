"""The learning numbers in one place: the bounded reweight formula, decay rule and compression threshold.

Edge reweighting (CUES / SHORTCUT / REPLACED_BY), bounded additive steps:

    pass:  w <- min(W_MAX, w + ALPHA)
    fail:  w <- max(W_MIN, w - BETA)

BETA > ALPHA on purpose: an edge that is right only half the time drifts down, and three straight failures take a
default edge (W_DEFAULT = 1.0) below W_FLOOR, so search stops suggesting it. Every reweighted edge also counts its
outcomes in `n_pass` / `n_fail` (extra edge properties, ignored by search) so decay can look at the record, not at w.

Decay (once per iteration, in maintain): an edge with n_fail >= DECAY_MIN_FAILS and n_fail > DECAY_RATIO * n_pass that
still sits at or above W_FLOOR is set to W_DECAYED (< W_FLOOR).

Learned cues start at W_LEARNED (above the floor, below what ingestion writes) and then move like any other edge.
Path compression needs COMPRESS_MIN_PASSES passing episodes (more passes than failures) on the same chain and term.
"""

from __future__ import annotations

from pinned.contract import W_DEFAULT, W_FLOOR

ALPHA = 0.25
BETA = 0.35
W_MIN = 0.0
W_MAX = 4.0
W_LEARNED = 0.6
W_DECAYED = W_FLOOR / 2
DECAY_MIN_FAILS = 3
DECAY_RATIO = 2
COMPRESS_MIN_PASSES = 2

assert W_MIN < W_DECAYED < W_FLOOR < W_LEARNED < W_DEFAULT < W_MAX


def bump(w: float | None, passed: bool) -> float:
    """One reweight step. A missing w (should not happen: contract says every edge carries it) counts as W_DEFAULT."""
    cur = W_DEFAULT if w is None else float(w)
    nxt = cur + ALPHA if passed else cur - BETA
    return round(min(W_MAX, max(W_MIN, nxt)), 6)


def should_decay(w: float | None, n_pass: int | None, n_fail: int | None) -> bool:
    """True when an edge keeps leading to failures and is still suggested (w >= W_FLOOR)."""
    cur = W_DEFAULT if w is None else float(w)
    fails, passes = int(n_fail or 0), int(n_pass or 0)
    return cur >= W_FLOOR and fails >= DECAY_MIN_FAILS and fails > DECAY_RATIO * passes


def shortcut_weight(passes: int) -> float:
    """A proven chain starts strong: W_DEFAULT plus one ALPHA per pass beyond the first, capped."""
    return round(min(W_MAX, W_DEFAULT + ALPHA * max(0, passes - 1)), 6)
