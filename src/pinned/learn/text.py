"""Problem text -> Term words (same shape as ingestion's Term keys: lower-case name parts, stopwords removed)."""

from __future__ import annotations

import re

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_.]*")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")

STOPWORDS = frozenset(
    """
    a about above after again against all also an and any are as at be because been before being below between both
    but by can could did do does doing down during each few for from further given had has have having he her here
    hers him his how i if in into is it its itself just let like make makes me more most must my no nor not now of off
    on once only or other our out over own same she should so some such than that the their them then there these they
    this those through to too under until up use used uses using very via was way we were what when where which while
    who whom why will with within would you your yours return returns write function code value values should new get
    takes take given input output result results based example need needs want one two first second
    """.split()
)

MIN_LEN = 3


def split_name(token: str) -> list[str]:
    """`DataFrame.to_csv` -> ["data", "frame", "to", "csv"]; dotted, snake and camel parts, lower-case."""
    out: list[str] = []
    for part in re.split(r"[._]+", token):
        out.extend(p.lower() for p in _CAMEL.findall(part))
    return out


def terms(text: str, limit: int | None = None) -> list[str]:
    """Distinct useful words of a text in first-seen order. A camel-case identifier also keeps its whole lower-case
    form (`DataFrame` -> "dataframe", "data", "frame"), because ingestion keys Terms by both."""
    seen: dict[str, None] = {}
    for tok in _WORD.findall(text or ""):
        words: list[str] = []
        for part in tok.split("."):
            if part.isidentifier() and "_" not in part:
                words.append(part.lower())
            words.extend(split_name(part))
        for w in words:
            if len(w) >= MIN_LEN and w not in STOPWORDS and not w.isdigit():
                seen.setdefault(w, None)
                if limit is not None and len(seen) >= limit:
                    return list(seen)
    return list(seen)
