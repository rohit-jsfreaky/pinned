"""Words that cue an API: parts of its name and words of its docstring's first line."""

from __future__ import annotations

import re

STOPWORDS = frozenset("""
a about above after again against all also am an and any are as at be because been before being below between both but
by can could did do does doing down during each else etc few for from further given had has have having here how i if
in into is it its itself just may more most must no nor not now of off on once only or other our out over own same
should so some such than that the their them then there these they this those through to too under until up upon use
used uses using very via was we were what when where which while who whom why will with within without would you your
return returns returned object objects instance value values new get set make one two default optional see e g ie eg
""".split())
GENERIC_PARTS = frozenset({"core", "api", "base", "utils", "util", "lib", "libs", "internal", "internals", "impl",
                           "common", "misc", "tools", "helpers", "compat", "init", "main"})
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\d|\b)|[A-Z]?[a-z]+|[A-Z]+|\d+")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]+")
_ROLE = re.compile(r":[A-Za-z]+:")  # reST roles such as :class: / :func:
MAX_DOC_WORDS = 12


def split_identifier(name: str) -> list[str]:
    """'DataFrame' -> ['data', 'frame']; 'from_numpy' -> ['from', 'numpy']; 'HTTPServer2' -> ['http', 'server', '2']."""
    out: list[str] = []
    for chunk in name.split("_"):
        out += [m.group(0).lower() for m in _CAMEL.finditer(chunk)]
    return out


def name_terms(path: str, import_names: list[str]) -> list[str]:
    """Terms from the dotted path after the import name: each segment whole (lower-case) and split into words."""
    parts = path.split(".")
    if parts and parts[0] in import_names:
        parts = parts[1:]
    out: list[str] = []
    for seg in parts:
        if seg.startswith("_") or seg.lower() in GENERIC_PARTS:
            continue
        cands = [seg.lower()] + split_identifier(seg)
        for c in cands:
            if len(c) >= 2 and c not in STOPWORDS and not c.isdigit() and c not in out:
                out.append(c)
    return out


def doc_terms(doc: str) -> list[str]:
    out: list[str] = []
    for m in _WORD.finditer(_ROLE.sub(" ", doc or "")):
        w = m.group(0).lower()
        if len(w) >= 3 and w not in STOPWORDS and w not in out:
            out.append(w)
        if len(out) >= MAX_DOC_WORDS:
            break
    return out


def cue_terms(path: str, doc: str, import_names: list[str]) -> dict[str, str]:
    """word -> source ("name" wins over "doc" when a word is in both)."""
    out = {w: "doc" for w in doc_terms(doc)}
    out.update({w: "name" for w in name_terms(path, import_names)})
    return out
