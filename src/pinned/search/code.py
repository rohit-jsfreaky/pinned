"""Static reading of the agent's code: which library APIs it references, resolved through import aliases.

Handles `import numpy as np`, `import scipy.special`, `from scipy import special`, `from x import y as z`, attribute
chains (`np.linalg.norm`), calls with their keyword arguments, and simple instance types
(`df = pd.DataFrame(...)`, `def f(df: pd.DataFrame)`, `pd.DataFrame(x).append(...)` -> `pandas.DataFrame.append`).
"""

from __future__ import annotations

import ast
import io
import tokenize
from dataclasses import dataclass, field

from .text import words


@dataclass(frozen=True)
class Ref:
    """One reference to a library API in the code."""

    path: str                          # resolved dotted import path, e.g. "pandas.DataFrame.append"
    line: int
    kind: str                          # "call" | "attr" | "import"
    keywords: tuple[str, ...] = ()     # keyword arguments passed (calls only)
    inferred: bool = False             # resolved through a variable's inferred instance type


@dataclass
class CodeRefs:
    refs: list[Ref] = field(default_factory=list)
    loose: list[str] = field(default_factory=list)   # method names called on objects whose type is unknown
    words: list[str] = field(default_factory=list)   # words from def/class names, docstrings and comments
    aliases: dict[str, str] = field(default_factory=dict)   # local name -> import path ("np" -> "numpy")
    error: str = ""                                  # syntax error message ("" = parsed fine)
    error_line: int | None = None


@dataclass(frozen=True)
class _Val:
    path: str
    instance: bool   # True = an object OF type `path` (not the module/class/function itself)


def _is_class_name(path: str) -> bool:
    last = path.rsplit(".", 1)[-1]
    return last[:1].isupper()


class _Reader:
    def __init__(self, tree: ast.AST, roots: set[str]):
        self.tree = tree
        self.roots = roots
        self.aliases: dict[str, str] = {}
        self.types: dict[str, str | None] = {}   # name -> instance type path (None = conflicting assignments)
        self.parents: dict[ast.AST, ast.AST] = {}
        self.refs: dict[tuple[str, int, str], Ref] = {}
        self.loose: dict[str, None] = {}

    # ------------------------------------------------------------------ pass 1: bindings
    def _ours(self, path: str) -> bool:
        return path.split(".", 1)[0] in self.roots

    def bind_imports(self) -> None:
        for n in ast.walk(self.tree):
            for child in ast.iter_child_nodes(n):
                self.parents[child] = n
            if isinstance(n, ast.Import):
                for a in n.names:
                    if not self._ours(a.name):
                        continue
                    if a.asname:
                        self.aliases[a.asname] = a.name
                    else:
                        root = a.name.split(".", 1)[0]
                        self.aliases[root] = root
                    if "." in a.name:
                        self._add(Ref(a.name, n.lineno, "import"))
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module and self._ours(n.module):
                for a in n.names:
                    if a.name == "*":
                        continue
                    path = f"{n.module}.{a.name}"
                    self.aliases[a.asname or a.name] = path
                    self._add(Ref(path, n.lineno, "import"))

    def bind_types(self) -> None:
        def note(name: str, path: str | None) -> None:
            if path is None:
                return
            if name in self.types and self.types[name] != path:
                self.types[name] = None
            else:
                self.types[name] = path

        for n in ast.walk(self.tree):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                v = self.resolve(n.value)
                note(n.targets[0].id, v.path if v and v.instance else None)
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                note(n.target.id, self._annotation(n.annotation))
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for arg in n.args.posonlyargs + n.args.args + n.args.kwonlyargs:
                    if arg.annotation is not None:
                        note(arg.arg, self._annotation(arg.annotation))

    def _annotation(self, ann: ast.expr) -> str | None:
        if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
            try:
                ann = ast.parse(ann.value, mode="eval").body
            except SyntaxError:
                return None
        v = self.resolve(ann)
        return v.path if v and not v.instance else None  # an annotation names a type, whatever its case

    # ------------------------------------------------------------------ resolution
    def resolve(self, e: ast.expr) -> _Val | None:
        if isinstance(e, ast.Name):
            if e.id in self.aliases:
                return _Val(self.aliases[e.id], False)
            t = self.types.get(e.id)
            return _Val(t, True) if t else None
        if isinstance(e, ast.Attribute):
            base = self.resolve(e.value)
            return _Val(f"{base.path}.{e.attr}", False) if base else None
        if isinstance(e, ast.Call):
            f = self.resolve(e.func)
            if f and not f.instance and _is_class_name(f.path):
                return _Val(f.path, True)
        return None

    # ------------------------------------------------------------------ pass 2: references
    def _add(self, ref: Ref) -> None:
        self.refs.setdefault((ref.path, ref.line, ref.kind), ref)

    def _through_instance(self, e: ast.expr) -> bool:
        while isinstance(e, ast.Attribute):
            e = e.value
        if isinstance(e, ast.Name):
            return e.id not in self.aliases and bool(self.types.get(e.id))
        return isinstance(e, ast.Call)

    def collect(self) -> None:
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Call):
                v = self.resolve(n.func)
                if v and not v.instance:
                    kws = tuple(k.arg for k in n.keywords if k.arg)
                    self._add(Ref(v.path, n.lineno, "call", kws, self._through_instance(n.func)))
                elif v is None and isinstance(n.func, ast.Attribute):
                    self.loose.setdefault(n.func.attr, None)
            elif isinstance(n, ast.Attribute):
                parent = self.parents.get(n)
                if isinstance(parent, ast.Attribute) and parent.value is n:
                    continue  # only the longest chain counts
                if isinstance(parent, ast.Call) and parent.func is n:
                    continue  # handled as a call
                v = self.resolve(n)
                if v and not v.instance:
                    self._add(Ref(v.path, n.lineno, "attr", (), self._through_instance(n)))


def _code_words(tree: ast.AST, code: str) -> list[str]:
    text: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            text.append(n.name)
            doc = ast.get_docstring(n)
            if doc:
                text.append(doc)
        elif isinstance(n, ast.Module):
            doc = ast.get_docstring(n)
            if doc:
                text.append(doc)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(code).readline):
            if tok.type == tokenize.COMMENT:
                text.append(tok.string.lstrip("#"))
    except (tokenize.TokenError, SyntaxError):
        pass
    return words("\n".join(text))


def read_code(code: str, import_names: set[str]) -> CodeRefs:
    """References to the library (whose top-level import names are `import_names`) in `code`. Never raises."""
    try:
        tree = ast.parse(code or "")
    except SyntaxError as e:
        return CodeRefs(error=f"{e.msg} (line {e.lineno})", error_line=e.lineno)
    except ValueError as e:  # e.g. source contains NUL bytes
        return CodeRefs(error=str(e))
    r = _Reader(tree, import_names)
    r.bind_imports()
    r.bind_types()
    r.collect()
    refs = sorted(r.refs.values(), key=lambda x: (x.line, x.kind != "import", x.path))
    return CodeRefs(refs=refs, loose=list(r.loose), words=_code_words(tree, code), aliases=dict(r.aliases))
