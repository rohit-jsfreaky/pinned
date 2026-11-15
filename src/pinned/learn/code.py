"""Which library APIs a piece of code actually touches (static, via ast).

Resolves names through imports and simple assignments:
  `import numpy as np; np.linalg.norm(x)`         -> numpy.linalg.norm
  `from scipy import special; special.i1(x)`       -> scipy.special.i1
  `from x import y as z; z()`                      -> x.y
  `f = np.linalg.norm; f(x)`                       -> numpy.linalg.norm
  `pd.DataFrame(d).append(r)`                      -> pandas.DataFrame + pandas.DataFrame.append (method on a new
                                                      instance of a class: the callee's last part is capitalised)
Calls on receivers we cannot resolve (`df.append(...)`) are kept by method name in `methods`.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field


@dataclass
class Call:
    path: str
    line: int
    keywords: list[str] = field(default_factory=list)
    positional: int = 0


@dataclass
class CodeUse:
    parsed: bool = True
    error: str = ""
    paths: set[str] = field(default_factory=set)      # every resolved dotted path into the library
    methods: set[str] = field(default_factory=set)    # attribute names called on unresolvable receivers
    modules: set[str] = field(default_factory=set)    # library modules the code imports
    aliases: dict[str, str] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)   # resolved calls in source order

    def resolve_tail(self, qualname: str) -> list[str]:
        """Resolved paths that end with `qualname` ("Ridge", "DataFrame.to_csv"), shortest first."""
        dotted = "." + qualname
        hits = {p for p in self.paths if p == qualname or p.endswith(dotted)}
        return sorted(hits, key=lambda p: (p.count("."), p))


def _root(path: str) -> str:
    return path.split(".", 1)[0]


class _Resolver:
    def __init__(self, roots: set[str]):
        self.roots = roots
        self.aliases: dict[str, str] = {}

    def resolve(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id)
        if isinstance(node, ast.Attribute):
            base = self.resolve(node.value)
            if base is not None:
                return f"{base}.{node.attr}"
            if isinstance(node.value, ast.Call):
                callee = self.resolve(node.value.func)
                if callee is not None and callee.rsplit(".", 1)[-1][:1].isupper():
                    return f"{callee}.{node.attr}"
        return None


def analyse(code: str, roots: set[str]) -> CodeUse:
    """Static use of the library whose import names are `roots` (e.g. {"sklearn"}). Never raises on bad code."""
    try:
        tree = ast.parse(code or "")
    except (SyntaxError, ValueError) as e:
        return CodeUse(parsed=False, error=f"{type(e).__name__}: {e}")
    use = CodeUse()
    r = _Resolver(roots)

    # imports first (anywhere in the file), so later references resolve regardless of nesting
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if _root(a.name) not in roots:
                    continue
                use.modules.add(a.name)
                use.paths.add(a.name)
                if a.asname:
                    r.aliases[a.asname] = a.name
                else:
                    r.aliases[_root(a.name)] = _root(a.name)
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level and _root(n.module) in roots:
            use.modules.add(n.module)
            for a in n.names:
                if a.name == "*":
                    continue
                full = f"{n.module}.{a.name}"
                r.aliases[a.asname or a.name] = full
                use.paths.add(full)

    # simple aliases: `f = np.linalg.norm` (one pass in source order is enough for straight-line code)
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            if isinstance(n.value, (ast.Name, ast.Attribute)):
                target = r.resolve(n.value)
                if target is not None:
                    r.aliases[n.targets[0].id] = target

    parent_of: dict[int, ast.AST] = {}
    for n in ast.walk(tree):
        for child in ast.iter_child_nodes(n):
            parent_of[id(child)] = n

    for n in ast.walk(tree):
        if isinstance(n, (ast.Name, ast.Attribute)) and isinstance(getattr(n, "ctx", None), ast.Load):
            parent = parent_of.get(id(n))
            if isinstance(parent, ast.Attribute) and parent.value is n:
                continue  # not maximal: the enclosing attribute chain is resolved instead
            path = r.resolve(n)
            if path is not None:
                use.paths.add(path)
        if isinstance(n, ast.Call):
            path = r.resolve(n.func)
            if path is not None:
                use.calls.append(
                    Call(path, n.lineno, [k.arg for k in n.keywords if k.arg], sum(not isinstance(a, ast.Starred) for a in n.args))
                )
            elif isinstance(n.func, ast.Attribute):
                use.methods.add(n.func.attr)
    use.aliases = dict(r.aliases)
    use.calls.sort(key=lambda c: c.line)
    return use
