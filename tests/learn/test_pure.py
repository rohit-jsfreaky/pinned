"""Pure parts of the learner (no graph): code analysis, error -> gap parsing, weights, terms, facts rule, split guard."""

import json

import pytest

from pinned.contract import ADDED_IN, REMOVED_IN, ContextItem, Hop
from pinned.graph import version_ord
from pinned.learn import SplitGuard, TestSplitError
from pinned.learn.code import analyse
from pinned.learn.errors import find_gaps
from pinned.learn.facts import ApiFacts, has_param
from pinned.learn.routes import route, route_string
from pinned.learn.text import terms
from pinned.learn.weights import ALPHA, BETA, W_DECAYED, W_MAX, W_MIN, bump, should_decay, shortcut_weight
from pinned.contract import W_FLOOR


def test_analyse_resolves_imports_aliases_and_methods():
    code = """
import numpy as np
import scipy.special
from scipy import linalg as la
from scipy.signal import convolve as conv
import pandas as pd

norm = np.linalg.norm
def f(x, df):
    a = np.linalg.norm(x, ord=2)
    b = scipy.special.i1(x)
    c = la.inv(x)
    d = conv(x, x, mode="same")
    e = norm(x)
    g = pd.DataFrame(x).append(x)
    return df.groupby("a").agg("sum")
"""
    use = analyse(code, {"numpy", "scipy", "pandas"})
    assert {"numpy.linalg.norm", "scipy.special.i1", "scipy.linalg.inv", "scipy.signal.convolve",
            "pandas.DataFrame", "pandas.DataFrame.append"} <= use.paths
    assert {"groupby", "agg"} <= use.methods
    assert "scipy.special" in use.modules and "numpy" in use.modules
    calls = {}
    for c in use.calls:
        calls.setdefault(c.path, c)
    assert calls["numpy.linalg.norm"].keywords == ["ord"]
    assert calls["scipy.signal.convolve"].keywords == ["mode"] and calls["scipy.signal.convolve"].positional == 2
    assert use.resolve_tail("DataFrame") == ["pandas.DataFrame"]


def test_analyse_ignores_other_libraries_and_bad_code():
    use = analyse("import os\nos.path.join('a')\nimport numpy\nnumpy.zeros(3)", {"numpy"})
    assert use.paths == {"numpy", "numpy.zeros"}
    bad = analyse("def (:", {"numpy"})
    assert not bad.parsed and "SyntaxError" in bad.error and not bad.paths


def test_find_gaps_every_shape():
    use = analyse("import pandas as pd\nfrom sklearn.linear_model import Ridge\npd.DataFrame(x)\nRidge(normalize=True)",
                  {"pandas", "sklearn"})
    roots = {"pandas", "sklearn"}

    def one(et, msg, lookup=None):
        gaps = find_gaps(et, msg, use, roots, lookup)
        assert len(gaps) == 1, gaps
        return gaps[0]

    g = one("AttributeError", "module 'pandas.io' has no attribute 'json_normalize'")
    assert (g.kind, g.path) == ("attribute", "pandas.io.json_normalize")
    g = one("AttributeError", "'DataFrame' object has no attribute 'append'")
    assert (g.kind, g.path) == ("attribute", "pandas.DataFrame.append")
    g = one("AttributeError", "type object 'Ridge' has no attribute 'fit_x'")
    assert g.path == "sklearn.linear_model.Ridge.fit_x"
    g = one("ImportError", "cannot import name 'Ridge2' from 'sklearn.linear_model' (/x/__init__.py)")
    assert (g.kind, g.path) == ("import", "sklearn.linear_model.Ridge2")
    g = one("ModuleNotFoundError", "No module named 'sklearn.cross_validation'")
    assert (g.kind, g.path) == ("import", "sklearn.cross_validation")
    g = one("TypeError", "Ridge.__init__() got an unexpected keyword argument 'normalize'")
    assert (g.kind, g.path, g.detail) == ("keyword", "sklearn.linear_model.Ridge", "normalize")
    g = one("TypeError", "DataFrame.to_csv() got an unexpected keyword argument 'line_terminator'")
    assert (g.path, g.detail) == ("pandas.DataFrame.to_csv", "line_terminator")
    g = one("TypeError", "Ridge() takes 1 positional argument but 2 were given")
    assert (g.kind, g.path) == ("signature", "sklearn.linear_model.Ridge")
    g = one("", "x.py:3: FutureWarning: pandas.DataFrame.append is deprecated, use pandas.concat instead")
    assert (g.kind, g.path, g.detail) == ("deprecated", "pandas.DataFrame.append", "pandas.concat")
    # a name the code never imported, found through the graph lookup
    g = one("TypeError", "read_json() got an unexpected keyword argument 'numpy'",
            lambda cands: {"pandas.read_json"} & set(cands))
    assert g.path == "pandas.read_json"


def test_find_gaps_ignores_unrelated_failures():
    use = analyse("import numpy as np", {"numpy"})
    assert find_gaps("AssertionError", "assert 3 == 4", use, {"numpy"}) == []
    assert find_gaps("ModuleNotFoundError", "No module named 'requests'", use, {"numpy"}) == []
    assert find_gaps("Timeout", "", use, {"numpy"}) == []


def test_bump_is_bounded():
    assert bump(1.0, True) == 1.0 + ALPHA and bump(1.0, False) == 1.0 - BETA
    assert bump(W_MAX, True) == W_MAX and bump(0.1, False) == W_MIN
    assert bump(None, True) == 1.0 + ALPHA  # missing w counts as W_DEFAULT
    w = 1.0
    for _ in range(3):
        w = bump(w, False)
    assert w < W_FLOOR  # three straight failures stop a default edge being suggested


def test_decay_rule_and_shortcut_weight():
    assert should_decay(1.0, 0, 3) and not should_decay(1.0, 0, 2)
    assert not should_decay(1.0, 2, 4) and should_decay(1.0, 2, 5)
    assert not should_decay(W_DECAYED, 0, 9)  # already below the floor
    assert shortcut_weight(2) == 1.0 + ALPHA and shortcut_weight(100) == W_MAX


def test_terms():
    ws = terms("Compute the modified Bessel function with DataFrame.to_csv and i1, then return 42")
    assert ws[:3] == ["compute", "modified", "bessel"]
    assert {"dataframe", "data", "frame", "csv"} <= set(ws)
    assert "the" not in ws and "42" not in ws and "i1" not in ws
    assert len(terms("alpha beta gamma delta", limit=2)) == 2


def test_facts_rule():
    v = version_ord
    f = ApiFacts([(v("1.0"), ADDED_IN, "(x)"), (v("2.0"), REMOVED_IN, ""), (v("3.0"), ADDED_IN, "(x, y)")],
                 [(v("1.5"), "deprecated", ""), (v("3.1"), "signature", "(x, y, z)")]).sort()
    assert f.status_at(v("0.9")) == "added_later"
    assert f.status_at(v("1.0")) == "valid" and f.status_at(v("1.5")) == "deprecated"
    assert f.status_at(v("2.5")) == "removed"
    assert f.signature_at(v("3.0")) == "(x, y)" and f.signature_at(v("3.2")) == "(x, y, z)"
    assert ApiFacts().status_at(v("1.0")) == "missing"
    assert has_param("(x, normalize=False)", "normalize") and has_param("(a, *, b: int = 1)", "b")
    assert not has_param("(x, **normalize_kw)", "normalize") and not has_param("(xnormalize=1)", "normalize")


def test_route_keeps_only_contiguous_api_hops():
    it = ContextItem("l", "m.c", "valid", [Hop("m.a", "REMOVED_IN", "l 2.0"), Hop("m.a", "REPLACED_BY", "m.b"),
                                          Hop("m.b", "ALIAS_OF", "m.c")])
    assert route_string(route(it)) == "m.a>m.b>m.c"
    assert route(ContextItem("l", "m.z", "valid", [Hop("m.a", "REPLACED_BY", "m.b")])) == []


def test_split_guard(tmp_path):
    p = tmp_path / "split.json"
    p.write_text(json.dumps({"seed": 13, "train": ["1", "2"], "test": ["3", 4]}))
    g = SplitGuard(p)
    g.check("1")
    with pytest.raises(TestSplitError, match="held-out"):
        g.check("4")
    SplitGuard(None).check("3")  # live use: no split
    with pytest.raises(FileNotFoundError):
        SplitGuard(tmp_path / "nope.json")
    (tmp_path / "bad.json").write_text("{}")
    with pytest.raises(ValueError):
        SplitGuard(tmp_path / "bad.json")
