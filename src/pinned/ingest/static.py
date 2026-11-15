"""Static load of one release's sources with griffe (no code is imported or run).

Public API = what a user can write starting from a public import name:
- modules: every submodule whose name has no leading underscore (test packages skipped);
- module members: names listed in `__all__` when the module defines it, else every non-underscore name defined in
  the module; names a module imports count as re-exports only via `__all__` or in a re-export hub (a package
  `__init__` or a module named `api`) — `from pandas import DataFrame` inside `pandas.io.sql` is not API;
  imports of other projects are always skipped;
- class members: public methods / attributes / nested classes, inherited ones included when the base class is in the
  same distribution (so `Child.method` exists when `method` is defined on `Base`).
A re-export keeps a `target` = the path where griffe found the object defined.
"""

from __future__ import annotations

import ast
import logging
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import griffe

from .inspect_worker import summary_line
from .deprecation import scan_class, scan_docstring, scan_function, scan_module_getattr
from .model import ApiRecord, clean_signature

log = logging.getLogger(__name__)

SKIP_MODULES = {"tests", "test", "testing_utils", "conftest", "_tests", "benchmarks", "setup"}
MAX_DEPTH = 12          # dotted segments; guards against alias cycles
MAX_DEFAULT = 60        # characters kept of one default value


@dataclass
class StaticResult:
    apis: dict[str, ApiRecord] = field(default_factory=dict)
    modules: dict[str, Path] = field(default_factory=dict)          # public module path -> file
    unresolved: dict[str, str] = field(default_factory=dict)        # public path -> alias target griffe could not find
    computed_all: set[str] = field(default_factory=set)             # modules whose __all__ is built at runtime
    imports: dict[str, set[str]] = field(default_factory=dict)      # module path -> absolute import targets
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------------------------------- rendering
def first_line(doc: str | None) -> str:
    for line in (doc or "").splitlines():
        line = line.strip()
        if line:
            return line[:200]
    return ""


def summary(name: str, doc: str | None) -> str:
    """First docstring line, skipping a leading "name(params) -> T" signature (even one wrapped over lines)."""
    return summary_line(name, doc or "")


def _default(value: object) -> str:
    text = clean_signature(" ".join(str(value).split()))
    return text if len(text) <= MAX_DEFAULT else text[: MAX_DEFAULT - 3] + "..."


def render_params(params: list[tuple[str, str, str | None]], drop_first: bool) -> str:
    """params = [(name, kind, default)] with griffe ParameterKind values. -> "(a, /, b=1, *args, c=None, **kw)"."""
    if drop_first and params and params[0][1] in ("positional-only", "positional or keyword") \
            and params[0][0] in ("self", "cls", "mcs", "metacls"):
        params = params[1:]
    out: list[str] = []
    saw_star = False
    for i, (name, kind, default) in enumerate(params):
        if kind == "variadic positional":
            out.append(f"*{name}")
            saw_star = True
            continue
        if kind == "variadic keyword":
            out.append(f"**{name}")
            continue
        if kind == "keyword-only" and not saw_star:
            out.append("*")
            saw_star = True
        out.append(f"{name}={_default(default)}" if default is not None else name)
        nxt = params[i + 1][1] if i + 1 < len(params) else None
        if kind == "positional-only" and nxt != "positional-only":
            out.append("/")
    return "(" + ", ".join(out) + ")"


def _function_params(fn: griffe.Function) -> list[tuple[str, str, str | None]]:
    return [(p.name, p.kind.value if p.kind else "positional or keyword",
             None if p.default is None or (p.kind and p.kind.value.startswith("variadic")) else str(p.default))
            for p in fn.parameters]


def _is_static(fn: griffe.Function) -> bool:
    return "staticmethod" in fn.labels


def wildcard_sources(source: str, module: str, is_package: bool) -> list[str]:
    """Absolute names of the modules a module star-imports (`from .core import *`), anywhere in its top level."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            return []
    out = []
    package = module if is_package else module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                name = ".".join(base + ([node.module] if node.module else []))
            else:
                name = node.module or ""
            if name and name not in out:
                out.append(name)
    return out


def type_checking_names(source: str) -> set[str]:
    """Names imported only under `if TYPE_CHECKING:` / `if t.TYPE_CHECKING:` (not there at runtime, so not API).
    griffe flags plain `TYPE_CHECKING` / `typing.TYPE_CHECKING` but not an aliased module (`import typing as t`)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            return set()
    out: set[str] = set()
    for node in getattr(tree, "body", []):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        name = test.id if isinstance(test, ast.Name) else test.attr if isinstance(test, ast.Attribute) else ""
        if name != "TYPE_CHECKING":
            continue
        for stmt in node.body:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                out |= {(a.asname or a.name).split(".")[0] for a in stmt.names}
    return out


# ---------------------------------------------------------------------------------------------------- loader
class StaticLoader:
    def __init__(self, root: Path, top_level: list[str], import_names: list[str], relaxed: set[str] = frozenset()):
        self.root = Path(root)
        self.relaxed = set(relaxed)  # modules walked in full: every own re-export counts, `__all__` or not (fill_gap)
        self.top_level = top_level
        self.import_names = import_names
        self.loader = griffe.GriffeLoader(search_paths=[str(self.root)], allow_inspection=False, store_source=True)
        self.result = StaticResult()

    def _own(self, path: str) -> bool:
        return path.split(".", 1)[0] in self.top_level

    def load(self) -> StaticResult:
        for name in self.top_level:
            try:
                self.loader.load(name)
            except Exception as e:  # one broken top-level must not sink the whole release
                self.result.errors.append(f"load {name}: {type(e).__name__}: {e}"[:300])
        try:
            self.loader.resolve_aliases(implicit=True, external=False)
        except Exception as e:
            self.result.errors.append(f"resolve_aliases: {type(e).__name__}: {e}"[:300])
        for name in self.import_names:
            mod = self.loader.modules_collection.members.get(name)
            if mod is not None:
                self._walk_module(mod, name)
        return self.result

    # ------------------------------------------------------------------ modules
    def _walk_module(self, mod: griffe.Module, path: str) -> None:
        if path in self.result.apis or path.count(".") >= MAX_DEPTH:
            return
        doc = mod.docstring.value if mod.docstring else ""
        rec = ApiRecord(path=path, kind="module", doc=first_line(doc))
        typing_only: set[str] = set()
        # `from X import *` is an explicit re-export even in a plain module (plotly.graph_objects re-exports
        # plotly.graph_objs that way): names griffe expanded from such a star count as re-exports
        star_from: list[str] = []
        if mod.filepath and not isinstance(mod.filepath, list):
            self.result.modules[path] = Path(mod.filepath)
            rec.deprecations = scan_docstring(doc)
            src = self._file_text(Path(mod.filepath))
            if "__getattr__" in src:
                rec.deprecations += scan_module_getattr(src)
            if "TYPE_CHECKING" in src:
                typing_only = type_checking_names(src)
            if "__all__" in src and not self._literal_exports(mod):
                self.result.computed_all.add(path)  # e.g. `__all__ = [s for s in dir() ...]`: only runtime knows
            if "import *" in src:
                star_from = wildcard_sources(src, mod.path, mod.is_init_module)
        self.result.apis[path] = rec
        # import targets incl. wildcard sources and names griffe already expanded from a wildcard (via .pyi stubs)
        self.result.imports[path] = set(mod.imports.values()) | {
            m.target_path for m in mod.members.values() if m.is_alias}
        exports = None if path in self.relaxed else self._exports(mod)
        hub = mod.is_init_module or mod.name == "api" or mod.filepath is None or path in self.relaxed

        for name, member in list(mod.members.items()):
            if name.endswith("*"):  # a wildcard griffe could not expand (target not found statically)
                continue
            # griffe's own `runtime` flag is not used: it is also False for code in a plain if/else (numpy's whole
            # top level sits under `if __NUMPY_SETUP__: ... else:`); type-checking-only imports are found above
            if name.startswith("_") or name in typing_only:
                continue  # private, or imported only for type checkers
            if member.is_alias:
                starred = any(member.target_path.startswith(f"{m}.") for m in star_from)
                if (exports is not None and name not in exports) or (exports is None and not hub and not starred):
                    continue
                self._alias(member, f"{path}.{name}", exported=exports is not None or path in self.relaxed,
                            expand=path in self.relaxed)
            elif member.is_module:
                if name not in SKIP_MODULES:
                    self._walk_module(member, f"{path}.{name}")
            elif exports is None or name in exports:
                self._object(member, f"{path}.{name}", in_class=False)
        if mod.filepath and not isinstance(mod.filepath, list):
            self._runtime_all_wildcards(mod, path, exports)
        if exports is not None:  # names in __all__ that static loading cannot find (built at runtime / compiled)
            for name in exports:
                if not name.startswith("_") and name not in mod.members and f"{path}.{name}" not in self.result.apis:
                    self.result.unresolved[f"{path}.{name}"] = path

    def _runtime_all_wildcards(self, mod: griffe.Module, path: str, exports: set[str] | None) -> None:
        """`from X import *` where X builds `__all__` at runtime (`[_ for _ in dir() if not _.startswith('_')]`):
        griffe reads that `__all__` as empty and expands the star to nothing. Python gives every public name of X,
        so those become re-exports here (librosa: `from .core import *` -> librosa.load)."""
        src = self._file_text(Path(mod.filepath))
        if "import *" not in src:
            return
        for source in wildcard_sources(src, mod.path, mod.is_init_module):
            if not self._own(source):
                continue
            try:
                smod = self.loader.modules_collection.get_member(source)
            except Exception:
                continue
            if smod is None or not getattr(smod, "is_module", False) or self._literal_exports(smod):
                continue
            for name, member in list(smod.members.items()):
                if name.startswith("_") or name.endswith("*") or name in mod.members:
                    continue
                if exports is not None and name not in exports:
                    continue
                child = f"{path}.{name}"
                if child in self.result.apis or (not member.is_alias and member.is_module):
                    continue
                if member.is_alias:  # _alias handles targets griffe cannot resolve (recorded as unresolved)
                    self._alias(member, child, exported=True)
                else:
                    self._object(member, child, in_class=False)

    @staticmethod
    def _literal_exports(mod: griffe.Module) -> bool:
        """True when griffe could read `__all__` as plain names (not absent, empty or partly computed)."""
        return bool(mod.exports) and all(isinstance(e, str) for e in mod.exports)

    @staticmethod
    def _exports(mod: griffe.Module) -> set[str] | None:
        if not mod.exports:  # absent, or computed at runtime (`[s for s in dir() ...]`) so it reads as empty
            return None
        return {e if isinstance(e, str) else getattr(e, "name", str(e)) for e in mod.exports}

    @staticmethod
    def _file_text(p: Path) -> str:
        try:
            return p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    # ------------------------------------------------------------------ aliases (re-exports)
    def _alias(self, alias: griffe.Alias, path: str, exported: bool, expand: bool = False) -> None:
        target_path = alias.target_path
        if not self._own(target_path):
            return  # imported from another project
        try:
            target = alias.final_target
        except Exception:
            # defined in a compiled module or built at runtime: dynamic inspection fills it
            self.result.unresolved[path] = target_path
            return
        if target.is_module:
            if exported and path not in self.result.apis:
                doc = target.docstring.value if target.docstring else ""
                self.result.apis[path] = ApiRecord(path=path, kind="module", doc=first_line(doc), target=target.path)
                if expand:  # a gap-filled module: also its module aliases' members (matplotlib.pyplot.style.use)
                    for name, member in list(target.members.items()):
                        if name.startswith("_") or name.endswith("*") or (not member.is_alias and member.is_module):
                            continue
                        if member.is_alias:
                            self._alias(member, f"{path}.{name}", exported=False)
                        else:
                            self._object(member, f"{path}.{name}", in_class=False)
            return
        self._object(target, path, in_class=False)

    # ------------------------------------------------------------------ functions / classes / attributes
    def _object(self, obj: griffe.Object, path: str, in_class: bool) -> None:
        if path in self.result.apis or path.count(".") >= MAX_DEPTH:
            return
        if obj.is_alias:
            try:
                obj = obj.final_target
            except Exception:
                self.result.unresolved[path] = obj.target_path
                return
            if not self._own(obj.path):
                return
        target = obj.path if obj.path != path else ""
        if obj.is_function:
            fn = obj
            doc = fn.docstring.value if fn.docstring else ""
            kind = "method" if in_class else "function"
            rec = ApiRecord(path=path, kind=kind, sig=render_params(_function_params(fn), in_class and not _is_static(fn)),
                            doc=summary(fn.name, doc), target=target)
            if {"property", "cached_property"} & set(fn.labels) or "property" in fn.labels:
                rec.kind, rec.sig = "attr", ""
            rec.deprecations = self._source_deprecations(fn, scan_function) + scan_docstring(doc)
            self.result.apis[path] = rec
        elif obj.is_class:
            self._class(obj, path, target)
        elif obj.is_attribute or obj.kind.value == "type alias":
            doc = obj.docstring.value if obj.docstring else ""
            self.result.apis[path] = ApiRecord(path=path, kind="attr", doc=first_line(doc), target=target)

    def _class(self, cls: griffe.Class, path: str, target: str) -> None:
        doc = cls.docstring.value if cls.docstring else ""
        rec = ApiRecord(path=path, kind="class", doc=summary(cls.name, doc), target=target)
        members = self._class_members(cls)
        init = members.get("__init__")
        if init is not None and init.is_function:
            rec.sig = render_params(_function_params(init), True)
            rec.deprecations += self._source_deprecations(init, scan_function, only_object=True)
        rec.deprecations = self._source_deprecations(cls, scan_class) + scan_docstring(doc) + rec.deprecations
        self.result.apis[path] = rec
        for name, member in members.items():
            if name.startswith("_"):
                continue
            self._object(member, f"{path}.{name}", in_class=True)

    def _class_members(self, cls: griffe.Class) -> dict[str, griffe.Object]:
        """Own members + members inherited from bases of the same distribution (MRO order, own first)."""
        out: dict[str, griffe.Object] = {}
        try:
            mro = [cls] + [b for b in cls.mro() if self._own(b.path)]
        except Exception:
            mro = [cls]
        for c in mro:
            for name, member in c.members.items():
                if name not in out:
                    out[name] = member
        return out

    def _source_deprecations(self, obj: griffe.Object, scan, only_object: bool = False) -> list:
        try:
            src = obj.source
        except Exception:
            return []
        if not src:
            return []
        found = scan(src)
        return [d for d in found if d.scope == "object"] if only_object else found


def add_namespace_inits(root: Path, top_level: list[str]) -> list[str]:
    """griffe does not load implicit namespace SUBpackages (a folder of .py files without __init__.py inside a
    regular package, e.g. flask/sansio in Flask 3.0), so their classes never resolve as bases. Python imports them
    all the same; for API purposes an empty __init__.py is equivalent, so one is added to the extracted copy.
    Returns the dotted names of the folders that got one."""
    added = []
    for top in top_level:
        pkg = Path(root) / top
        if not (pkg / "__init__.py").exists():
            continue
        for d in sorted(p for p in pkg.rglob("*") if p.is_dir()):
            rel = d.relative_to(root)
            if not all(part.isidentifier() for part in rel.parts) or (d / "__init__.py").exists():
                continue
            if (d.parent / "__init__.py").exists() and any(d.glob("*.py")):
                (d / "__init__.py").write_text("")
                added.append(".".join(rel.parts))
    return added


def load_static(root: Path, top_level: list[str], import_names: list[str], relaxed: set[str] = frozenset()) -> StaticResult:
    logging.getLogger("griffe").setLevel(logging.ERROR)
    add_namespace_inits(root, top_level)
    with warnings.catch_warnings():  # griffe parses foreign code: silence its SyntaxWarnings
        warnings.simplefilter("ignore", SyntaxWarning)
        return StaticLoader(root, top_level, import_names, relaxed).load()
