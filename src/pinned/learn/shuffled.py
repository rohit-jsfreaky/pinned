"""ShuffledSearch — the negative control. Wraps any `Search` and returns contexts of the SAME size whose ranking
carries no information: either the inner items with their scores shuffled (mode "weights"), or random valid items
for that library/version (mode "items"). A gain that survives this control is not coming from the graph's ranking."""

from __future__ import annotations

import hashlib
import random
from collections.abc import Callable
from dataclasses import replace
from typing import Literal

from pinned.contract import ApiTimeline, Context, ContextItem, Finding, Search

Universe = Callable[[str, str], list[str]]
Mode = Literal["weights", "items"]


class ShuffledSearch:
    """`seed` makes every answer reproducible (per problem). In "items" mode the random items come from `universe`
    (e.g. `PlainStore.valid_paths`), else from the valid items the inner search has returned so far for that
    library/version; if the pool is too small the rest is filled with the inner items, shuffled."""

    def __init__(self, search: Search, seed: int, mode: Mode = "items", universe: Universe | None = None):
        if mode not in ("weights", "items"):
            raise ValueError("mode must be 'weights' or 'items'")
        self.inner, self.seed, self.mode, self.universe = search, seed, mode, universe
        self._pool: dict[tuple[str, str], set[str]] = {}

    def _rng(self, problem: str, library: str, version: str) -> random.Random:
        h = hashlib.sha256(f"{self.seed}|{library}|{version}|{problem}".encode()).hexdigest()
        return random.Random(int(h[:16], 16))

    def context_for(self, problem: str, starting_code: str, library: str, version: str, k: int = 8) -> Context:
        ctx = self.inner.context_for(problem, starting_code, library, version, k)
        rng = self._rng(problem, library, version)
        items = list(ctx.items)
        pool = self._pool.setdefault((library, version), set())
        pool.update(it.path for it in items if it.status in ("valid", "deprecated"))
        if self.mode == "weights" or not items:
            out = _shuffle_scores(items, rng)
        else:
            out = self._random_items(items, library, version, rng)
        return replace(ctx, items=out)

    def _random_items(self, items: list[ContextItem], library: str, version: str, rng: random.Random) -> list[ContextItem]:
        n = len(items)
        given = {it.path for it in items}
        universe = self.universe(library, version) if self.universe else sorted(self._pool[(library, version)])
        choices = sorted(set(universe) - given)
        picked = rng.sample(choices, min(n, len(choices)))
        scores = sorted((it.score for it in items), reverse=True)
        out = [ContextItem(library=library, path=p, status="valid", note="random valid item (negative control)",
                           score=scores[i]) for i, p in enumerate(picked)]
        if len(out) < n:
            rest = _shuffle_scores(items, rng)[: n - len(out)]
            start = len(out)
            out.extend(replace(it, score=scores[start + i]) for i, it in enumerate(rest))
        return out  # random items carry the inner scores top-down, so the list is still ordered by score

    def check_calls(self, code: str, library: str, version: str) -> list[Finding]:
        return self.inner.check_calls(code, library, version)

    def explain_api(self, library: str, path: str) -> ApiTimeline:
        return self.inner.explain_api(library, path)


def _shuffle_scores(items: list[ContextItem], rng: random.Random) -> list[ContextItem]:
    """Same items, scores permuted at random, re-ranked by the shuffled score."""
    scores = [it.score for it in items]
    order = list(range(len(items)))
    rng.shuffle(order)
    out = [replace(items[j], score=scores[i]) for i, j in enumerate(order)]
    return sorted(out, key=lambda it: -it.score)
