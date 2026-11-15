"""Arms: what extra context the solver gets for a problem. Every arm feeds the SAME prompt template."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from pinned.contract import Context, Search

from .dataset import Problem
from .docs import DocsFetcher

DOCS_BUDGET_CHARS = 8000   # fixed budget for the docs baseline (all URLs of a problem together)
GRAPH_K = 8


@dataclass
class ArmContext:
    text: str                              # goes into the prompt's context block ("" = no block)
    context: Context | None = None         # graph arms: the structured Context (Episodes carry it to the learner)
    meta: dict = field(default_factory=dict)


class Arm(Protocol):
    name: str

    def describe(self) -> dict: ...
    def context(self, problem: Problem) -> ArmContext: ...


class NoneArm:
    """The agent alone: no context."""

    name = "none"

    def describe(self) -> dict:
        return {"name": self.name, "kind": "no context"}

    def context(self, problem: Problem) -> ArmContext:
        return ArmContext("")


class DocsArm:
    """PLAIN-DOCS BASELINE: text of the problem's official `docs` URLs (fetched once, cleaned, truncated)."""

    name = "docs"
    HEADER = ("Reference documentation (plain text of the official docs pages for this library version, "
              "truncated):")

    def __init__(self, fetcher: DocsFetcher, budget_chars: int = DOCS_BUDGET_CHARS):
        self.fetcher = fetcher
        self.budget = budget_chars

    def describe(self) -> dict:
        return {"name": self.name, "kind": "plain docs baseline (official docs URLs from the dataset)",
                "budget_chars": self.budget, "cache_dir": str(self.fetcher.cache_dir)}

    def context(self, problem: Problem) -> ArmContext:
        urls = list(dict.fromkeys(problem.docs))
        pages = [self.fetcher.get(u) for u in urls]
        good = [p for p in pages if p.ok and p.text.strip()]
        meta = {"urls": len(urls), "fetched": len(good), "failed": [p.url for p in pages if not (p.ok and p.text)]}
        if not good:
            return ArmContext("", meta=meta)
        share = self.budget // len(good)
        chunks = []
        for p in good:
            body = p.text if len(p.text) <= share else p.text[:share].rsplit("\n", 1)[0] + "\n[...truncated]"
            chunks.append(f"--- {p.url}\n{body}")
        meta["chars"] = sum(len(c) for c in chunks)
        return ArmContext(self.HEADER + "\n" + "\n".join(chunks), meta=meta)


class GraphArm:
    """Pinned: any `Search` (contract) -> `ctx.to_prompt()`. Used for the static graph (iteration 0) and the curve."""

    def __init__(self, search: Search, name: str = "pinned", k: int = GRAPH_K):
        self.search = search
        self.name = name
        self.k = k

    def describe(self) -> dict:
        return {"name": self.name, "kind": "graph search", "search": type(self.search).__name__, "k": self.k}

    def context(self, problem: Problem) -> ArmContext:
        ctx = self.search.context_for(problem.problem, problem.starting_code, problem.library, problem.version, self.k)
        text = ctx.to_prompt() if ctx.items else ""
        return ArmContext(text, ctx, {"items": len(ctx.items), "query_ms": ctx.query_ms,
                                      "graph_queries": ctx.graph_queries})
