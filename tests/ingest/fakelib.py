"""A fake library with three releases, built in a tmp dir, plus a fake PyPI client and a local inspector.

Each release has two trees: `src/` = what static loading sees (the .py/.pyi of a wheel; the "compiled" module
`_speed` is missing, like a .so) and `full/` = what is importable at runtime (with `_speed.py` standing in for the
extension module). The local inspector runs the REAL inspect_worker.py on this interpreter against `full/`; only the
venv install step of VenvInspector is replaced.

What changes between releases (the facts the tests expect):
  old_func     1.0 present, 1.1 removed
  rename_me    1.1 warns "rename_me is deprecated, use new_name instead" (new_name added in 1.1), 1.2 removed
  sig_change   (a, b=1) -> 1.1 (a, b=1, *, c=None)
  flaky        1.0 present, 1.1 removed, 1.2 re-added
  tool         <pkg>.tool re-exports <pkg>.tools.tool (ALIAS_OF)
  fast_add     only in the compiled module _speed, star-imported by the package (dynamic inspection)
  mover        <pkg>.core.mover moves to <pkg>.extra.mover in 1.2 (REPLACED_BY, same object moved)
  lazy_thing   <pkg>.lazy creates it at runtime via __getattr__/__dir__ (only fill_gap sees it)
  gone_attr    never exists; 1.2's package source says "gone_attr was removed, use new_name instead" (fill_gap)
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

from pinned.ingest.dynamic import DynamicResult, VenvInspector
from pinned.ingest.pypi import Extracted, Release

VERSIONS = ("1.0.0", "1.1.0", "1.2.0")


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text).lstrip())


def build(base: Path, pkg: str) -> dict[str, Path]:
    """Create the three releases of `pkg`; returns {version: release dir}."""
    out = {}
    for v in VERSIONS:
        rel_dir = base / v
        src = rel_dir / "src" / pkg
        minor = int(v.split(".")[1])
        names = ["tool", "sig_change", "flaky", "Thing", "rename_me", "new_name", "old_func", "fast_add"]
        core = ["import warnings", "",
                "class Thing:", '    """A thing that holds things."""', "    def __init__(self, size=3):",
                "        self.size = size", "    def grow(self, by):", '        """Grow the thing."""', "        return by", ""]
        if minor == 0:
            core += ["def old_func(x):", '    """Old function that goes away."""', "    return x", ""]
        if minor < 2:
            core += ["def rename_me(x):", '    """Will be renamed."""']
            if minor == 1:
                core += ['    warnings.warn("rename_me is deprecated, use new_name instead", DeprecationWarning)']
            core += ["    return x", ""]
            core += ["def mover(x):", '    """Moves house in 1.2."""', "    return x", ""]
        if minor >= 1:
            core += ["def new_name(x):", '    """The new name."""', "    return x", ""]
        core += ["def sig_change(a, b=1" + (", *, c=None" if minor >= 1 else "") + "):",
                 '    """Signature changes in 1.1."""', "    return a", ""]
        if minor != 1:
            core += ["def flaky():", '    """Gone in 1.1, back in 1.2."""', "    return 1", ""]
        _write(src.parent, f"{pkg}/core.py", "\n".join(core) + "\n")
        _write(src.parent, f"{pkg}/tools.py", '''
            def tool(path, mode="r"):
                """Open a tool."""
                return path
        ''')
        exported = [n for n in names if (n != "old_func" or minor == 0) and (n not in ("rename_me",) or minor < 2)
                    and (n != "new_name" or minor >= 1) and (n != "flaky" or minor != 1)]
        imports = [n for n in exported if n not in ("tool", "fast_add")]
        init = [f'"""Fake library {v}."""', f"from .core import {', '.join(imports)}", "from .tools import tool",
                "from ._speed import *", "from . import lazy"]
        if minor < 2:
            init += ["from .core import mover"]
            exported.append("mover")
        else:
            init += ["from .extra import mover"]
            exported.append("mover")
            init += ["", "__former_attrs__ = {", '    "gone_attr": "gone_attr was removed in 1.1, use new_name instead",', "}"]
        init += ["", f"__all__ = {sorted(set(exported) | {'tools', 'lazy'})!r}"]
        _write(src.parent, f"{pkg}/__init__.py", "\n".join(init) + "\n")
        if minor >= 2:
            _write(src.parent, f"{pkg}/extra.py", '''
                def mover(x):
                    """Moves house in 1.2."""
                    return x
            ''')
        _write(src.parent, f"{pkg}/lazy.py", '''
            """Builds an attribute at runtime."""

            def __getattr__(name):
                if name == "lazy_thing":
                    def lazy_thing(n, scale=2.0):
                        """Made at runtime."""
                        return n * scale
                    return lazy_thing
                raise AttributeError(name)

            def __dir__():
                return ["lazy_thing"]
        ''')
        full = rel_dir / "full"
        import shutil
        shutil.copytree(src.parent, full, dirs_exist_ok=True)
        _write(full, f"{pkg}/_speed.py", '''
            __all__ = ["fast_add"]

            def fast_add(x, y):
                """Add two numbers fast (stands in for a C function)."""
                return x + y
        ''')
        out[v] = rel_dir
    return out


class FakeClient:
    """Implements the part of PyPIClient the ingestor uses."""

    def __init__(self, library: str, pkg: str, dirs: dict[str, Path]):
        self.library, self.pkg, self.dirs = library, pkg, dirs

    def releases(self, name: str) -> list[Release]:
        assert name == self.library
        return [Release(v, f"2026-0{i + 1}-01", [{"filename": f"{self.pkg}-{v}-py3-none-any.whl",
                                                    "packagetype": "bdist_wheel"}]) for i, v in enumerate(VERSIONS)]

    def fetch_sources(self, library: str, release: Release, python: str = "") -> Extracted:
        d = self.dirs[release.version]
        return Extracted(d / "src", f"{self.pkg}-{release.version}-py3-none-any.whl", "pure-wheel", [self.pkg],
                         [f"{self.pkg}._speed"])

    def close(self) -> None:
        pass


class LocalInspector(VenvInspector):
    """VenvInspector without the venv: the real worker on this interpreter, the release's `full/` tree on sys.path."""

    def __init__(self, work_dir: Path, dirs: dict[str, Path]):
        super().__init__(work_dir)
        self.dirs = dirs
        self.calls: list[tuple[str, list[str]]] = []

    def inspect(self, library, pypi_version, released, python, files, dep_sets, modules, owners) -> DynamicResult:
        self.calls.append((pypi_version, list(modules)))
        work = self.work_dir / f"{pypi_version}-{len(self.calls)}"
        work.mkdir(parents=True, exist_ok=True)
        # each call is a fresh process: no module cache leaks between releases
        return self.run_worker(sys.executable, modules, owners, work, [str(self.dirs[pypi_version] / "full")])
