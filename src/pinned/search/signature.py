"""Parse signature strings ("(a, b=1, *, c: int = 2, **kw) -> X") and explain keyword-argument changes."""

from __future__ import annotations

from dataclasses import dataclass

from pinned.contract import ADDED_IN, CHANGED_IN

from .validity import Event

POSITIONAL_ONLY, NORMAL, VAR_POSITIONAL, KEYWORD_ONLY, VAR_KEYWORD = "pos", "normal", "*", "kw", "**"
_OPEN, _CLOSE = "([{", ")]}"


@dataclass(frozen=True)
class Params:
    names: tuple[str, ...]              # every named parameter in order (no * / ** names)
    keyword_names: frozenset[str]       # names that can be passed as keyword=...
    var_keyword: bool                   # has **kwargs: any keyword is accepted
    known: bool                         # False when there was no signature to parse

    def accepts(self, keyword: str) -> bool:
        return not self.known or self.var_keyword or keyword in self.keyword_names


UNKNOWN = Params((), frozenset(), False, False)


def _param_list(sig: str) -> str | None:
    """Text between the first '(' and its matching ')' (quotes and nesting respected)."""
    start = sig.find("(")
    if start < 0:
        return None
    depth, quote, escaped = 0, "", False
    for i in range(start, len(sig)):
        ch = sig[i]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth -= 1
            if depth == 0:
                return sig[start + 1 : i]
    return None


def _split_top(text: str) -> list[str]:
    parts: list[str] = []
    cur: list[str] = []
    depth, quote, escaped = 0, "", False
    for ch in text:
        if quote:
            cur.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
            continue
        if ch in "'\"":
            quote = ch
        elif ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur).strip())
    return parts


def parse_params(sig: str) -> Params:
    """Parse a signature string; unknown/unparseable -> UNKNOWN (accepts everything, so no false alarms)."""
    inner = _param_list(sig or "")
    if inner is None:
        return UNKNOWN
    names: list[str] = []
    keyword: set[str] = set()
    var_kw = False
    kind = NORMAL
    raw = _split_top(inner)
    if "/" in raw:  # everything before "/" is positional-only
        cut = raw.index("/")
        names += [_name(p) for p in raw[:cut] if _name(p)]
        raw = raw[cut + 1 :]
    for p in raw:
        if p == "*":
            kind = KEYWORD_ONLY
            continue
        if p.startswith("**"):
            var_kw = True
            continue
        if p.startswith("*"):
            kind = KEYWORD_ONLY
            continue
        n = _name(p)
        if n and n not in ("self", "cls"):
            names.append(n)
            keyword.add(n)
    return Params(tuple(names), frozenset(keyword), var_kw, True)


def _name(param: str) -> str:
    head = param.split("=", 1)[0].split(":", 1)[0].strip()
    return head if head.isidentifier() else ""


@dataclass(frozen=True)
class KeywordFix:
    keyword: str
    replacement: str          # the parameter to use instead ("" = none known)
    event: Event | None       # the CHANGED_IN / ADDED_IN event that shows the rename


def _history(events: list[Event]) -> list[tuple[Event, Params, Params]]:
    """(event, params before, params after) for every signature change, incl. a re-add with a new sig."""
    out = []
    for e in events:
        if e.rel == CHANGED_IN and e.kind == "signature":
            out.append((e, parse_params(e.before), parse_params(e.after)))
    adds = sorted((e for e in events if e.rel == ADDED_IN and e.sig), key=lambda e: e.ord)
    for prev, cur in zip(adds, adds[1:]):
        if prev.sig != cur.sig:
            out.append((cur, parse_params(prev.sig), parse_params(cur.sig)))
    return out


def keyword_fix(keyword: str, events: list[Event], now: Params) -> KeywordFix:
    """For a keyword that `now` (the signature at v) does not accept: which parameter replaced it, from the history.

    A change that drops `keyword` and adds exactly one name -> rename to that name (going forward). A change that adds
    `keyword` and drops exactly one name -> at an older version the parameter was that name (going back).
    The suggested name must be accepted at v.
    """
    for e, before, after in sorted(_history(events), key=lambda h: h[0].ord):
        gone = set(before.keyword_names) - set(after.keyword_names)
        new = set(after.keyword_names) - set(before.keyword_names)
        if keyword in gone and len(new) == 1:
            cand = next(iter(new))
        elif keyword in new and len(gone) == 1:
            cand = next(iter(gone))
        else:
            continue
        if cand in now.keyword_names:
            return KeywordFix(keyword, cand, e)
    return KeywordFix(keyword, "", None)
