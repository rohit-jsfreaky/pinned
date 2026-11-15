"""Words of a task description / code -> Term words (the same normalisation ingestion uses: lower-case name parts)."""

from __future__ import annotations

import re

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DOTTED = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z0-9])|[A-Z]?[a-z0-9]+|[A-Z]+")

STOPWORDS = frozenset(
    """
    a about above after again against all also am an and any are as at be because been before being below between both
    but by can could did do does doing down during each either else ever every few for from further get gets given had
    has have having he her here hers him his how however i if in into is it its itself just let like make makes may me
    might more most must my need needs new no nor not now of off on once one only or other our out over own per please
    same shall she should so some such than that the their them then there these they this those through to too two
    under until up upon us use used uses using very via want was we were what when where whether which while who whom
    why will with within without would you your yours function method class value values return returns returned
    result results write implement create given code following task provided example input output inputs outputs
    def self cls none true false import from lambda pass raise yield assert global nonlocal elif try except finally
    print str int float bool list dict tuple set object type args kwargs
    """.split()
)


def split_identifier(token: str) -> list[str]:
    """snake_case / CamelCase / dotted -> lower-case parts ("read_csv" -> read, csv; "DataFrame" -> data, frame)."""
    parts: list[str] = []
    for chunk in re.split(r"[._]+", token):
        parts += [p.lower() for p in _CAMEL.findall(chunk)]
    return [p for p in parts if p]


def words(text: str) -> list[str]:
    """Distinct Term candidates from free text, in first-seen order: each token, its whole lower-case form when it is a
    compound (read_csv), and its parts. Stopwords, 1-letter words and pure numbers are dropped."""
    seen: dict[str, None] = {}
    for tok in _WORD.findall(text or ""):
        cands = split_identifier(tok)
        whole = tok.lower().strip("_")
        if len(cands) > 1 and whole:
            cands = [whole] + cands
        for w in cands:
            if len(w) > 1 and not w.isdigit() and w not in STOPWORDS:
                seen.setdefault(w, None)
    return list(seen)


def dotted_names(text: str) -> list[str]:
    """Dotted identifiers mentioned in free text ("DataFrame.append", "np.linalg.norm"), first-seen order."""
    return list(dict.fromkeys(m.group(0) for m in _DOTTED.finditer(text or "")))
