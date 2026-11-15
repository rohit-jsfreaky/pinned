"""Deprecation evidence in real code, and the replacement names it points at.

Found in three places:
- calls: `warnings.warn(msg, DeprecationWarning | FutureWarning | PendingDeprecationWarning)` (positional or
  `category=`), and helpers whose name says "deprecat" (`warn_deprecated`, `sympy_deprecation_warning`, ...).
- decorators whose name says "deprecat" (`@deprecated("...")`, `@_api.deprecated("3.4", alternative="x")`).
- docstrings: the `.. deprecated::` directive.
A warning inside an `if` / `try` / loop only fires for some calls (a parameter or a branch), so it is `partial`;
the same holds for decorators that deprecate an argument (`deprecate_kwarg`, `deprecate_nonkeyword_arguments`).
"""

from __future__ import annotations

import ast
import re
import textwrap
import warnings

from .model import Deprecation

_CATEGORIES = {"DeprecationWarning", "FutureWarning", "PendingDeprecationWarning"}
_PARTIAL_DECORATOR = re.compile(r"(kwarg|arg|param|nonkeyword|positional|alias)", re.I)
_TEXT_KEYS = ("message", "msg", "alternative", "instead", "replacement", "removal", "addendum", "deprecated_since_version",
              "since", "use_instead", "new_name", "new_arg_name", "old_arg_name", "name")
_MAX = 300


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return ""


def _text(node: ast.AST | None) -> str:
    """Best-effort literal text of a message expression (f-strings keep their {placeholders})."""
    if node is None:
        return ""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                parts.append(str(v.value))
            elif isinstance(v, ast.FormattedValue):
                parts.append("{" + ast.unparse(v.value) + "}")
        return "".join(parts)
    if isinstance(node, ast.BinOp):  # "a" + "b", "x %s" % y
        left, right = _text(node.left), _text(node.right)
        return left + right if isinstance(node.op, ast.Add) else left
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return _text(node.func.value)
    if isinstance(node, (ast.Tuple, ast.List)):
        return " ".join(t for t in (_text(e) for e in node.elts) if t)
    return ""


def _clean(msg: str) -> str:
    return " ".join(msg.split())[:_MAX]


def _call_message(call: ast.Call) -> str:
    texts = [_text(a) for a in call.args]
    for kw in call.keywords:
        if kw.arg in _TEXT_KEYS:
            t = _text(kw.value)
            if t:
                texts.append(f"{kw.arg}={t}" if kw.arg in ("alternative", "instead", "use_instead", "replacement",
                                                           "new_name", "new_arg_name") else t)
    return _clean(" ".join(t for t in texts if t))


def _is_deprecation_call(call: ast.Call) -> bool:
    name = _dotted(call.func)
    leaf = name.rsplit(".", 1)[-1]
    if leaf == "warn":
        cats = [_dotted(a) for a in call.args[1:2]] + [_dotted(k.value) for k in call.keywords if k.arg == "category"]
        return any(c.rsplit(".", 1)[-1] in _CATEGORIES or "Deprecat" in c for c in cats)
    return "deprecat" in leaf.lower() and leaf.lower() not in ("deprecated",)


class _Finder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.found: list[Deprecation] = []
        self._guard = 0  # depth inside if / try / loops / with

    def _guarded(self, node: ast.AST) -> None:
        self._guard += 1
        self.generic_visit(node)
        self._guard -= 1

    visit_If = visit_For = visit_While = visit_Try = visit_With = _guarded

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # nested functions belong to themselves
        return

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        if _is_deprecation_call(node):
            msg = _call_message(node) or _dotted(node.func)
            self.found.append(Deprecation(msg, "warn", "partial" if self._guard else "object"))
        self.generic_visit(node)


def _parse(source: str) -> ast.AST | None:
    """Parse foreign code quietly (old releases are full of invalid escape sequences)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return ast.parse(textwrap.dedent(source))
        except (SyntaxError, ValueError):
            return None


def _decorators(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> list[Deprecation]:
    out = []
    for d in node.decorator_list:
        name = _dotted(d)
        leaf = name.rsplit(".", 1)[-1]
        if "deprecat" not in leaf.lower():
            continue
        msg = _call_message(d) if isinstance(d, ast.Call) else ""
        scope = "partial" if _PARTIAL_DECORATOR.search(leaf.replace("deprecated", "").replace("deprecate", "")) else "object"
        out.append(Deprecation(_clean(f"@{name}: {msg}" if msg else f"@{name}"), "decorator", scope))
    return out


def scan_function(source: str) -> list[Deprecation]:
    """Evidence on one function / method definition (its decorators and its own body, not nested defs)."""
    tree = _parse(source)
    if tree is None:
        return []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            f = _Finder()
            for stmt in node.body:
                f.visit(stmt)
            return _decorators(node) + f.found
    return []


def scan_class(source: str) -> list[Deprecation]:
    """Evidence on a class: its decorators, and warnings at the top of `__init__` / `__new__` / class body."""
    tree = _parse(source)
    if tree is None:
        return []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            found = _decorators(node)
            for stmt in node.body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name in ("__init__", "__new__"):
                    f = _Finder()
                    for s in stmt.body:
                        f.visit(s)
                    found += f.found
                elif not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    f = _Finder()
                    f.visit(stmt)
                    found += f.found
            return found
    return []


def scan_module_getattr(source: str) -> list[Deprecation]:
    """A module whose `__getattr__` warns about deprecation (scipy's deprecated private-module shims, numpy aliases).
    Always `partial`: only some names go through `__getattr__`."""
    tree = _parse(source)
    if tree is None:
        return []
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.FunctionDef) and node.name == "__getattr__":
            f = _Finder()
            for stmt in node.body:
                f.visit(stmt)
            return [Deprecation(d.message, "getattr", "partial") for d in f.found]
    return []


_DIRECTIVE = re.compile(r"^\s*\.\.\s+deprecated::\s*(?P<ver>\S*)\s*(?P<rest>.*)$")


def scan_docstring(doc: str) -> list[Deprecation]:
    """`.. deprecated:: <version>` + its indented body."""
    if not doc:
        return []
    lines = doc.splitlines()
    out = []
    for i, line in enumerate(lines):
        m = _DIRECTIVE.match(line)
        if not m:
            continue
        indent = len(line) - len(line.lstrip())
        body = [m.group("rest")]
        for nxt in lines[i + 1:]:
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                break
            body.append(nxt.strip())
        text = _clean(" ".join(b for b in body if b))
        out.append(Deprecation(_clean(f"deprecated {m.group('ver')}: {text}".strip()), "docstring", "object"))
    return out


_GONE = re.compile(r"deprecat|removed|no longer|expired|alias|instead|renamed|moved", re.I)


def scan_module_strings(source: str, name: str) -> list[str]:
    """String literals of a module that talk about `name` going away (numpy's `__former_attrs__` / expired-attribute
    tables, `__getattr__` messages): the literal must mention `name` as a word and use a removal / deprecation word.
    Used by fill_gap for an attribute the release does not have."""
    tree = _parse(source)
    if tree is None:
        return []
    word = re.compile(rf"(?<![\w.]){re.escape(name)}(?![\w])")
    out: list[str] = []
    for node in ast.walk(tree):
        text = ""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.JoinedStr):
            text = _text(node)
        if text and word.search(text) and _GONE.search(text):
            msg = _clean(text)
            if msg not in out:
                out.append(msg)
        elif isinstance(node, ast.Dict):  # {"float": "`np.float` was a deprecated alias ..."}
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == name:
                    msg = _clean(_text(v))
                    if msg and msg not in out:
                        out.append(msg)
    return out


# ---------------------------------------------------------------------------------------------------- replacements
_NAME = r"[`'\".]{0,2}(?P<name>[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*)(?:\([^)]*\))?[`'\"]{0,2}"
_PATTERNS = [
    re.compile(p.format(name=_NAME), re.I)
    for p in (
        r"\buse\s+(?:the\s+)?(?:function\s+|method\s+|class\s+|module\s+|attribute\s+)?{name}\s+instead\b",
        r"\binstead\s*,?\s+use\s+(?:the\s+)?{name}",
        r"\bplease\s+use\s+(?:the\s+)?{name}",
        r"\brenamed\s+(?:to|as)\s+{name}",
        r"\bmoved\s+to\s+{name}",
        r"\breplaced\s+(?:by|with)\s+{name}",
        r"\bin\s+favou?r\s+of\s+{name}",
        r"\b(?:alternative|instead|use_instead|replacement|new_name)\s*=\s*{name}",
        r"\bdeprecated[;,:]?\s+use\s+{name}",
        r"\bshould\s+use\s+{name}",
        r"\buse\s+{name}\s+(?:rather|in\s+place)",
    )
]
_NOT_NAMES = {"the", "a", "an", "this", "that", "it", "them", "instead", "of", "to", "in", "which", "with", "for", "and",
              "or", "be", "is", "are", "not", "keyword", "argument", "parameter", "function", "method", "class", "module",
              "version", "np", "pd", "none", "true", "false", "self"}


def replacement_names(message: str) -> list[str]:
    """Candidate replacement names in a deprecation message ("use X instead", "renamed to X", ...), in order."""
    out: list[str] = []
    for pat in _PATTERNS:
        for m in pat.finditer(message):
            name = m.group("name").strip(".`")
            if name and name.lower() not in _NOT_NAMES and name not in out:
                out.append(name)
    return out


ABBREVIATIONS = {"np": "numpy", "pd": "pandas", "plt": "matplotlib.pyplot", "mpl": "matplotlib", "sp": "scipy",
                 "nx": "networkx", "sns": "seaborn", "tf": "tensorflow", "gpd": "geopandas", "gr": "gradio"}


def resolve_name(name: str, owner: str, known: set[str], import_names: list[str]) -> str | None:
    """Turn a name from a message into a real public path of the same library.

    `owner` is the deprecated API's path. Tries: the name as written, abbreviations (np. -> numpy.), relative to the
    owner's class, module and every parent package, then under each import name. First hit in `known` wins.
    """
    first, _, rest = name.partition(".")
    cands = [name]
    if first in ABBREVIATIONS and rest:
        cands.append(f"{ABBREVIATIONS[first]}.{rest}")
    parts = owner.split(".")
    for i in range(len(parts) - 1, 0, -1):
        cands.append(".".join(parts[:i] + [name]))
    cands += [f"{imp}.{name}" for imp in import_names]
    for c in cands:
        if c in known and c != owner:
            return c
    return None
