"""Pure parts of ingestion: no graph, no network."""

import zipfile

import pytest

from pinned.graph import version_ord
from pinned.ingest.dataset import Pair, pairs_from_rows
from pinned.ingest.deprecation import (
    replacement_names, resolve_name, scan_class, scan_docstring, scan_function, scan_module_strings,
)
from pinned.ingest.diff import canonical_paths, diff
from pinned.ingest.dynamic import install_attempts, python_candidates
from pinned.ingest.ladder import build_ladder
from pinned.ingest.model import ApiRecord, Deprecation, Snapshot
from pinned.ingest.pypi import PyPIClient, PyPIError, Release, _extract_wheel
from pinned.ingest.snapshot import compiled_plan, merge
from pinned.ingest.static import StaticResult, load_static, render_params, summary, type_checking_names
from pinned.ingest.terms import cue_terms, split_identifier


def _rel(v, day="2020-01-01", files=None, yanked=False):
    return Release(v, day, files or [{"filename": f"x-{v}-py3-none-any.whl", "packagetype": "bdist_wheel"}], yanked)


# ------------------------------------------------------------------ dataset + ladder
def test_pairs_use_only_pair_fields_and_pick_main_python():
    rows = [{"library": "Scipy", "version": "1.7.3", "python_version": "3.10", "additional_dependencies": "numpy==1.21.6"},
            {"library": "scipy", "version": "1.7.3", "python_version": "3.10", "additional_dependencies": ""},
            {"library": "scipy", "version": "1.7.3", "python_version": "3.7", "additional_dependencies": ""}]
    (p,) = pairs_from_rows(rows)
    assert (p.library, p.version, p.problems, p.python) == ("scipy", "1.7.3", 3, "3.10")
    assert p.dependency_sets == ["", "numpy==1.21.6"]


def test_ladder_rule_loose_versions_and_yanked():
    rels = [_rel(v) for v in ("1.9.0", "1.9.3", "2.0.0", "2.0.2", "2.1.0", "2.1.1", "2.2.0", "2.3.0", "2.3.5", "2.4.0")]
    rels.append(_rel("2.2.1", yanked=True))
    pairs = [Pair("lib", "2", 1, ["3.10"]), Pair("lib", "2.2.1", 1, ["3.9"])]
    lad = build_ladder("lib", pairs, sorted(rels, key=lambda r: version_ord(r.version)))
    got = [(r.version, r.pypi, r.needed) for r in lad.rungs]
    # one minor below (1.9) .. one minor above (2.3): latest live patch of each + exact needed versions
    assert got == [("1.9.3", "1.9.3", False), ("2", "2.0.0", True), ("2.0.2", "2.0.2", False),
                   ("2.1.1", "2.1.1", False), ("2.2.0", "2.2.0", False), ("2.2.1", "2.2.1", True),
                   ("2.3.5", "2.3.5", False)]
    assert lad.rung("2.0.0").version == "2" and lad.rung("2.2.1").yanked
    assert lad.rung("1.9.3").python == "3.10" and lad.rung("2.3.5").python == "3.9"   # nearest needed rung
    with pytest.raises(PyPIError):
        build_ladder("lib", [Pair("lib", "9.9")], rels)


# ------------------------------------------------------------------ PyPI files
def test_choose_file_prefers_pure_then_matching_cpython_then_sdist():
    files = [{"filename": "a-1.0-cp39-cp39-manylinux1_x86_64.whl", "packagetype": "bdist_wheel"},
             {"filename": "a-1.0-cp310-cp310-manylinux_2_17_x86_64.whl", "packagetype": "bdist_wheel"},
             {"filename": "a-1.0-cp310-cp310-win_amd64.whl", "packagetype": "bdist_wheel"},
             {"filename": "a-1.0.tar.gz", "packagetype": "sdist"}]
    assert PyPIClient.choose_file(_rel("1.0", files=files), "3.9")["filename"].startswith("a-1.0-cp39")
    assert "cp310" in PyPIClient.choose_file(_rel("1.0", files=files), "3.12")["filename"]
    pure = files + [{"filename": "a-1.0-py3-none-any.whl", "packagetype": "bdist_wheel"}]
    assert PyPIClient.choose_file(_rel("1.0", files=pure))["filename"] == "a-1.0-py3-none-any.whl"
    assert PyPIClient.choose_file(_rel("1.0", files=files[3:]))["filename"] == "a-1.0.tar.gz"
    nocp = [{"filename": "lightgbm-3.0.0-py2.py3-none-manylinux1_x86_64.whl", "packagetype": "bdist_wheel"}]
    assert PyPIClient.choose_file(_rel("3.0.0", files=nocp))["filename"] == nocp[0]["filename"]


def test_wheel_extraction_keeps_sources_and_finds_extension_modules(tmp_path):
    whl = tmp_path / "pkg-1.0-cp310-cp310-manylinux1_x86_64.whl"
    with zipfile.ZipFile(whl, "w") as z:
        z.writestr("pkg/__init__.py", "from ._fast import *\n")
        z.writestr("pkg/sub/mod.pyi", "def f(x: int) -> int: ...\n")
        z.writestr("pkg/_fast.cpython-310-x86_64-linux-gnu.so", b"\x7fELF...")
        z.writestr("pkg/plain.so", b"\x7fELF... PyInit_plain ...")
        z.writestr("pkg/lib_native.so", b"\x7fELF... no init here")
        z.writestr("pkg.libs/libopenblas-r0.3.so", b"\x7fELF")
        z.writestr("pkg/data.json", "{}")
        z.writestr("pkg-1.0.dist-info/top_level.txt", "pkg\n")
        z.writestr("../evil.py", "boom")
    ex = _extract_wheel(whl, tmp_path / "out")
    assert ex.top_level == ["pkg"] and ex.compiled == ["pkg._fast", "pkg.plain"]
    files = sorted(str(p.relative_to(tmp_path / "out")) for p in (tmp_path / "out").rglob("*") if p.is_file())
    assert files == ["pkg/__init__.py", "pkg/sub/mod.pyi"]


# ------------------------------------------------------------------ deprecation evidence
def test_deprecation_scanning_scopes_and_sources():
    src = '''
@deprecated("use bar instead")
def f(x, y=None):
    if y is not None:
        warnings.warn("y is deprecated", FutureWarning)
    warnings.warn(f"f is going away, use {g.__name__} instead", category=DeprecationWarning, stacklevel=2)
    warnings.warn("just a user warning", UserWarning)
    def inner():
        warnings.warn("inner", DeprecationWarning)
'''
    found = scan_function(src)
    assert [(d.source, d.scope) for d in found] == [("decorator", "object"), ("warn", "partial"), ("warn", "object")]
    assert found[0].message == "@deprecated: use bar instead"
    cls = scan_class('''
@_api.deprecated("3.4", alternative="NewThing")
class Old:
    def __init__(self):
        sympy_deprecation_warning("Old is deprecated", deprecated_since_version="1.9", active_deprecations_target="x")
    def other(self):
        warnings.warn("not about the class", DeprecationWarning)
''')
    assert [d.source for d in cls] == ["decorator", "warn"] and "alternative=NewThing" in cls[0].message
    assert scan_function("@deprecate_kwarg(old_arg_name='a', new_arg_name='b')\ndef h(b): pass")[0].scope == "partial"
    doc = scan_docstring("Thing.\n\n.. deprecated:: 1.2\n    Use `fk.Other` instead.\n\nMore text.")
    assert doc[0].message == "deprecated 1.2: Use `fk.Other` instead."


def test_replacement_names_and_resolution():
    assert replacement_names("DataFrame.append is deprecated. Use pandas.concat instead.") == ["pandas.concat"]
    assert replacement_names("``comb`` is deprecated in favour of ``scipy.special.comb``") == ["scipy.special.comb"]
    assert replacement_names("'from_json' is deprecated. Use 'from_file(path, json.load)' instead.") == ["from_file"]
    assert replacement_names("renamed to `new_thing`; moved to np.linalg.solve") == ["new_thing", "np.linalg.solve"]
    known = {"pandas.concat", "numpy.linalg.solve", "pkg.mod.Cls.new", "pkg.helper"}
    assert resolve_name("np.linalg.solve", "numpy.foo", known, ["numpy"]) == "numpy.linalg.solve"
    assert resolve_name("new", "pkg.mod.Cls.old", known, ["pkg"]) == "pkg.mod.Cls.new"
    assert resolve_name("helper", "pkg.mod.thing", known, ["pkg"]) == "pkg.helper"
    assert resolve_name("nowhere", "pkg.mod.thing", known, ["pkg"]) is None


def test_module_strings_find_removal_messages():
    src = '__former_attrs__ = {"float": "`np.float` was a deprecated alias for the builtin `float`.", "int": "x"}\n' \
          'msg = "floaty is unrelated"\n'
    assert scan_module_strings(src, "float") == ["`np.float` was a deprecated alias for the builtin `float`."]
    assert scan_module_strings(src, "missing") == []


# ------------------------------------------------------------------ names, terms, signatures
def test_terms_and_identifier_split():
    assert split_identifier("HTTPServer2") == ["http", "server", "2"]
    cues = cue_terms("pandas.core.frame.DataFrame.append", "Append rows of :class:`other` to the end.", ["pandas"])
    assert cues["append"] == "name" and cues["dataframe"] == "name" and cues["rows"] == "doc"
    assert "pandas" not in cues and "core" not in cues and "class" not in cues and "the" not in cues


def test_render_params_and_summary():
    params = [("self", "positional or keyword", None), ("a", "positional-only", None), ("b", "positional-only", "1"),
              ("c", "positional or keyword", "'x'"), ("d", "keyword-only", "None"), ("kw", "variadic keyword", None)]
    assert render_params(params, drop_first=True) == "(a, b=1, /, c='x', *, d=None, **kw)"
    assert render_params([("x", "positional or keyword", "a" * 100)], False).endswith("...)")
    assert summary("relu", "relu(input) -> Tensor\n\nApplies the rectified linear unit.") == "Applies the rectified linear unit."


def test_type_checking_imports_are_not_reexports():
    src = "import typing as t\nif t.TYPE_CHECKING:\n    from ..app import Flask\nfrom .x import y\n"
    assert type_checking_names(src) == {"Flask"}


def test_static_load_of_a_small_package(tmp_path):
    pkg = tmp_path / "toy"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from .impl import Widget, make\nfrom ._hidden import secret\n"
                                     "import os\n__all__ = ['Widget', 'make', 'sub']\n")
    (pkg / "impl.py").write_text("from os.path import join\nclass Base:\n    def ping(self, n=1):\n        '''Ping.'''\n"
                                 "class Widget(Base):\n    '''A widget.'''\n    def __init__(self, size, *, color='red'):\n"
                                 "        pass\n    @property\n    def area(self):\n        return 1\n"
                                 "def make(kind):\n    '''Make one.'''\n")
    (pkg / "_hidden.py").write_text("def secret(): pass\n")
    (pkg / "sub.py").write_text("def g(): pass\n")
    res = load_static(tmp_path, ["toy"], ["toy"])
    a = res.apis
    assert a["toy.Widget"].kind == "class" and a["toy.Widget"].sig == "(size, *, color='red')"
    assert a["toy.Widget"].target == "toy.impl.Widget"
    assert a["toy.Widget.ping"].kind == "method" and a["toy.Widget.ping"].target == "toy.impl.Base.ping"
    assert a["toy.Widget.area"].kind == "attr"
    assert "toy.secret" not in a and "toy.os" not in a and "toy.impl.join" not in a
    assert a["toy.sub.g"].kind == "function" and a["toy.make"].doc == "Make one."


# ------------------------------------------------------------------ dynamic planning + merge
def test_compiled_plan_and_merge():
    static = StaticResult(apis={"p": ApiRecord("p", "module"), "p.sub": ApiRecord("p.sub", "module"),
                                "p.f": ApiRecord("p.f", "attr", target="p._c.f")},
                          imports={"p": {"p._c.f", "os"}, "p.sub": {"p.sub.x"}}, unresolved={"p.sub.lazy": "p.sub"})
    assert compiled_plan(static, ["p._c", "p.fastpub"]).modules == ["p", "p.fastpub", "p.sub"]
    apis = dict(static.apis)
    n = merge(apis, [ApiRecord("p.f", "function", sig="(x)", origin="dynamic"),
                     ApiRecord("p.g", "function", sig="(y)", origin="dynamic")], ["p._c"])
    assert n == 2 and apis["p.f"].kind == "function" and apis["p.f"].sig == "(x)" and apis["p.g"].origin == "dynamic"


def test_python_candidates_and_install_attempts():
    files = [{"filename": "a-1-cp38-cp38-manylinux1_x86_64.whl"}, {"filename": "a-1-cp39-cp39-manylinux1_x86_64.whl"}]
    assert python_candidates("3.10", files) == ["3.10", "3.9", "3.8"]
    assert python_candidates("3.9", files) == ["3.9"]
    att = install_attempts("scipy", "1.7.3", "2021-11-24", ["", "numpy==1.21.6"])
    assert att[0] == ["scipy==1.7.3", "griffe", "numpy==1.21.6"]
    assert att[1][:2] == ["--exclude-newer", "2022-03-24"] and att[-1] == ["scipy==1.7.3", "griffe"]
    torch = install_attempts("torch", "1.9.0", "2021-06-15", [""])
    assert all(a[:2] == ["--index", "https://download.pytorch.org/whl/cpu"] for a in torch)


# ------------------------------------------------------------------ diff
def _snap(version, *recs):
    return Snapshot("lib", version, ["lib"], f"lib-{version}.whl", apis={r.path: r for r in recs})


def test_diff_events():
    dep = Deprecation("old is deprecated, use lib.new instead", "warn")
    prev = _snap("1.0", ApiRecord("lib", "module"), ApiRecord("lib.old", "function", sig="(a)"),
                 ApiRecord("lib.keep", "function", sig="(a)"), ApiRecord("lib.m.Cls", "class"),
                 ApiRecord("lib.m", "module"), ApiRecord("lib.m.Cls.go", "method", sig="()"))
    cur = _snap("1.1", ApiRecord("lib", "module"), ApiRecord("lib.old", "function", sig="(a)", deprecations=[dep]),
                ApiRecord("lib.keep", "function", sig="(a, b=2)"), ApiRecord("lib.new", "function", sig="(a)"),
                ApiRecord("lib.n", "module"), ApiRecord("lib.n.Cls", "class"), ApiRecord("lib.n.Cls.go", "method", sig="()"),
                ApiRecord("lib.Cls", "class", target="lib.n.Cls"))
    f = diff(prev, cur, "1.1", "1.0")
    assert set(f.added) == {"lib.new", "lib.n", "lib.n.Cls", "lib.n.Cls.go", "lib.Cls"}
    assert set(f.removed) == {"lib.m", "lib.m.Cls", "lib.m.Cls.go"}
    kinds = {(c.path, c.kind) for c in f.changed}
    assert kinds == {("lib.keep", "signature"), ("lib.old", "deprecated")}
    assert {(r.path, r.by) for r in f.replaced} == {("lib.old", "lib.new"), ("lib.m.Cls", "lib.n.Cls"),
                                                    ("lib.m.Cls.go", "lib.n.Cls.go")}
    assert f.aliases == {"lib.Cls": "lib.n.Cls"} and f.members["lib.n.Cls.go"] == "lib.n.Cls"
    first = diff(None, prev, "1.0")
    assert set(first.added) == set(prev.apis) and "first ingested version" in first.added["lib.old"]


def test_canonical_path_prefers_public_definition_then_shortest():
    s = _snap("1", ApiRecord("lib.a.B", "class", target="lib._x.B"), ApiRecord("lib.B", "class", target="lib._x.B"),
              ApiRecord("lib.c.D", "class"), ApiRecord("lib.D", "class", target="lib.c.D"))
    assert canonical_paths(s) == {"lib.a.B": "lib.B", "lib.D": "lib.c.D"}
