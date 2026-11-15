"""Read a failure (error_type + error_text) and name the library module / attribute it is about.

Recognised (Python 3.7-3.12 message shapes):
  AttributeError  module 'scipy.special' has no attribute 'i1x'          -> attribute  scipy.special.i1x
                  'DataFrame' object has no attribute 'append'           -> attribute  <class path>.append
                  type object 'Foo' has no attribute 'bar'               -> attribute  <class path>.bar
  ImportError     cannot import name 'x' from 'a.b' (...)                -> import     a.b.x
  ModuleNotFound  No module named 'a.b'                                  -> import     a.b
  TypeError       f() got an unexpected keyword argument 'k'             -> keyword    <f path>   (detail = k)
                  Ridge.__init__() got an unexpected keyword argument 'k'-> keyword    <Ridge path>
                  f() takes 2 positional arguments but 3 were given      -> signature  <f path>
                  f() missing 1 required positional argument: 'x'        -> signature  <f path>
  Deprecation/FutureWarning text: names in the warning line              -> deprecated <path> (detail = "use Y" if any)
Class and function names are resolved through what the code imported (`CodeUse`); a `lookup` callback asks the graph
which candidate paths exist when the code alone cannot tell.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from .code import CodeUse

GapKind = Literal["attribute", "import", "keyword", "signature", "deprecated"]


@dataclass(frozen=True)
class Gap:
    kind: GapKind
    path: str          # resolved dotted path the failure is about (the hint passed to fill_gap)
    detail: str = ""   # keyword name, or the replacement a warning names
    message: str = ""  # the matching error line, trimmed


_MODULE_ATTR = re.compile(r"module '(?P<mod>[\w.]+)' has no attribute '(?P<attr>\w+)'")
_OBJECT_ATTR = re.compile(r"(?:type object '|(?<!module )')(?P<cls>\w+)' (?:object )?has no attribute '(?P<attr>\w+)'")
_CANNOT_IMPORT = re.compile(r"cannot import name '(?P<name>\w+)' from '(?P<mod>[\w.]+)'")
_NO_MODULE = re.compile(r"No module named '(?P<mod>[\w.]+)'")
_KEYWORD = re.compile(r"(?P<func>[\w.<>]+)\(\) got an unexpected keyword argument '(?P<kw>\w+)'")
_POSITIONAL = re.compile(r"(?P<func>[\w.<>]+)\(\) (?:takes|missing) ")
_WARNING_LINE = re.compile(r"[^\n]*(?:DeprecationWarning|FutureWarning|PendingDeprecationWarning|deprecated)[^\n]*",
                           re.IGNORECASE)
_DOTTED = re.compile(r"[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+")
_QUOTED = re.compile(r"[`'\"]([A-Za-z_][\w.]*)(?:\(\))?[`'\"]")
_INSTEAD = re.compile(r"(?:use|replaced by|renamed to|moved to)\s+[`'\"]?([A-Za-z_][\w.]*)", re.IGNORECASE)

Lookup = Callable[[list[str]], set[str]]


def _line(text: str, m: re.Match) -> str:
    start = text.rfind("\n", 0, m.start()) + 1
    end = text.find("\n", m.end())
    return text[start : end if end >= 0 else len(text)].strip()[:300]


class _Resolver:
    def __init__(self, use: CodeUse, roots: set[str], lookup: Lookup | None):
        self.use, self.roots, self.lookup = use, roots, lookup

    def qualname(self, qual: str) -> str | None:
        """'Ridge', 'Ridge.__init__', 'DataFrame.to_csv', 'scipy.special.i1' -> one resolved library path or None."""
        qual = qual.replace("<locals>.", "")
        parts = [p for p in qual.split(".") if p]
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts:
            return None
        qual = ".".join(parts)
        if parts[0] in self.roots:
            return qual
        hits = self.use.resolve_tail(qual)
        if hits:
            return hits[0]
        # `DataFrame.to_csv` with DataFrame known: class path + rest
        if len(parts) > 1:
            head = self.use.resolve_tail(parts[0])
            if head:
                return ".".join([head[0], *parts[1:]])
        return self._graph_guess(qual)

    def _graph_guess(self, qual: str) -> str | None:
        if self.lookup is None:
            return None
        bases = set(self.use.modules) | {m.rsplit(".", 1)[0] for m in self.use.paths if "." in m} | self.roots
        candidates = sorted({f"{b}.{qual}" for b in bases})
        found = self.lookup(candidates) if candidates else set()
        return min(found, key=lambda p: (p.count("."), p)) if found else None

    def in_library(self, path: str) -> bool:
        return path.split(".", 1)[0] in self.roots


def find_gaps(error_type: str, error_text: str, use: CodeUse, roots: set[str], lookup: Lookup | None = None) -> list[Gap]:
    """Every library fact a failure points at, deduplicated, in the order found."""
    text = f"{error_type}: {error_text}" if error_type and error_type not in (error_text or "") else (error_text or "")
    res = _Resolver(use, roots, lookup)
    gaps: list[Gap] = []

    def add(kind: GapKind, path: str | None, detail: str, m: re.Match) -> None:
        if path and res.in_library(path):
            gaps.append(Gap(kind, path, detail, _line(text, m)))

    for m in _MODULE_ATTR.finditer(text):
        add("attribute", f"{m['mod']}.{m['attr']}", "", m)
    for m in _OBJECT_ATTR.finditer(text):
        cls = res.qualname(m["cls"])
        add("attribute", f"{cls}.{m['attr']}" if cls else None, "", m)
    for m in _CANNOT_IMPORT.finditer(text):
        add("import", f"{m['mod']}.{m['name']}", "", m)
    for m in _NO_MODULE.finditer(text):
        add("import", m["mod"], "", m)
    for m in _KEYWORD.finditer(text):
        add("keyword", res.qualname(m["func"]), m["kw"], m)
    for m in _POSITIONAL.finditer(text):
        add("signature", res.qualname(m["func"]), "", m)
    for m in _WARNING_LINE.finditer(text):
        line = m.group(0)
        if "Error" in line.split(":", 1)[0] and "Warning" not in line:
            continue  # an error line that merely contains the word "deprecated" is handled above
        replacement = _first_resolved(res, (x.group(1) for x in _INSTEAD.finditer(line)))
        names = [x.group(0) for x in _DOTTED.finditer(line)] + [x.group(1) for x in _QUOTED.finditer(line)]
        for name in names:
            path = res.qualname(name)
            if path and path != replacement and res.in_library(path):
                add("deprecated", path, replacement or "", m)
                break
    return list(dict.fromkeys(gaps))


def _first_resolved(res: _Resolver, names: Iterable[str]) -> str | None:
    for name in names:
        path = res.qualname(name.rstrip("."))
        if path:
            return path
    return None
