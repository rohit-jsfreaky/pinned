"""Candidate scoring for context_for. Deterministic; the learned edge weights `w` drive it, so learning moves answers.

score(api) = CODE_SCORE                 if the starting code references it
           + GUESS_SCORE                if the task text names it ("DataFrame.append") and the graph knows it
           + sum over words t:  q(t) * w(t, api) * idf(t)          for CUES edges (w >= W_FLOOR)
           + sum over words t:  q(t) * SHORTCUT_BOOST * w(t, api)  for SHORTCUT edges valid at the asked version
           x MEMBER_BOOST              if it is a member of something the code references (pandas.DataFrame.*)
idf(t) = 1 / (1 + ln(number of APIs of this library that t cues, w >= W_FLOOR))  - a word that cues many APIs counts
         less. The count comes from facts.TermStats (learned when the word is expanded).
q(t)   = 1.0 for task words, LOOSE_Q for method names called on unknown objects, CODE_WORD_Q for code words.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from pinned.contract import SHORTCUT

from .facts import Cue

CODE_SCORE = 3.0
GUESS_SCORE = 2.0
SHORTCUT_BOOST = 2.0
MEMBER_BOOST = 1.5
TASK_Q = 1.0
LOOSE_Q = 1.0
CODE_WORD_Q = 0.5


@dataclass
class Candidate:
    api: int
    path: str
    score: float = 0.0
    from_code: bool = False
    guessed: bool = False
    cues: list[tuple[float, Cue]] = field(default_factory=list)   # (contribution, edge) behind the score


def idf(degree: int) -> float:
    return 1.0 / (1.0 + math.log(max(degree, 1)))


def score_cues(cands: dict[int, Candidate], cue_rows: list[Cue], q: dict[str, float], degree: dict[str, int]) -> None:
    """Add every edge's contribution. `degree[word]` = CUES edges of that word into this library (w >= W_FLOOR)."""
    for c in cue_rows:
        if c.rel == SHORTCUT:
            contrib = q.get(c.term, TASK_Q) * SHORTCUT_BOOST * c.w
        else:
            contrib = q.get(c.term, TASK_Q) * c.w * idf(degree.get(c.term, 1))
        cand = cands.setdefault(c.api, Candidate(c.api, c.path))
        cand.score += contrib
        cand.cues.append((contrib, c))


def apply_member_boost(cands: dict[int, Candidate], code_paths: set[str]) -> None:
    prefixes = tuple(p + "." for p in code_paths)
    if not prefixes:
        return
    for c in cands.values():
        if not c.from_code and c.cues and c.path.startswith(prefixes):
            c.score *= MEMBER_BOOST


def ranked(cands: dict[int, Candidate]) -> list[Candidate]:
    return sorted(cands.values(), key=lambda c: (-c.score, c.path))
