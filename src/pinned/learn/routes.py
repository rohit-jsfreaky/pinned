"""Shared helpers: node ids for the schema, which context items the code used, and the Api->Api route behind an item."""

from __future__ import annotations

from pinned.contract import ALIAS_OF, API, EPISODE, LIBRARY, REPLACED_BY, TERM, VERSION, Context, ContextItem, Hop
from pinned.graph import node_id

from .code import CodeUse

ROUTE_RELS = (REPLACED_BY, ALIAS_OF)


def api_id(library: str, path: str) -> int:
    return node_id(API, f"{library}|{path}")


def term_id(word: str) -> int:
    return node_id(TERM, word)


def version_id(library: str, version: str) -> int:
    return node_id(VERSION, f"{library}|{version}")


def library_id(library: str) -> int:
    return node_id(LIBRARY, library)


def episode_id(key: str) -> int:
    return node_id(EPISODE, key)


def _is_method_path(path: str) -> bool:
    parts = path.split(".")
    return len(parts) >= 3 and parts[-2][:1].isupper()


def used_items(ctx: Context | None, use: CodeUse) -> list[ContextItem]:
    """Context items the code actually used: resolved path match, or a method called on an unresolvable receiver whose
    name matches a `Class.method` item (`df.append(...)` uses `pandas.DataFrame.append`)."""
    if ctx is None:
        return []
    out: list[ContextItem] = []
    for it in ctx.items:
        if it.path in use.paths or (_is_method_path(it.path) and it.path.rsplit(".", 1)[-1] in use.methods):
            out.append(it)
    return out


def route(item: ContextItem) -> list[Hop]:
    """The contiguous REPLACED_BY / ALIAS_OF hops of the item's chain that end at the item's path (oldest first).
    Empty when the item came straight from a cue (no Api->Api hop)."""
    hops = [h for h in item.chain if h.rel in ROUTE_RELS]
    out: list[Hop] = []
    head = item.path
    seen = {head}
    for h in reversed(hops):
        if h.dst == head and h.src not in seen:
            out.insert(0, h)
            head = h.src
            seen.add(head)
    return out


def route_string(hops: list[Hop]) -> str:
    """"a>b>c" for hops a->b, b->c (paths never contain '>')."""
    return ">".join([hops[0].src] + [h.dst for h in hops]) if hops else ""
