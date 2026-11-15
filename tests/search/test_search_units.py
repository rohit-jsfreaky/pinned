"""Pure parts of search (no graph): validity rule, signatures, words, code reading."""

from pinned.contract import ADDED_IN, CHANGED_IN, REMOVED_IN
from pinned.graph import version_ord
from pinned.search.code import read_code
from pinned.search.signature import keyword_fix, parse_params
from pinned.search.text import dotted_names, split_identifier, words
from pinned.search.validity import Event, signature_at, verdict_at


def ev(rel, ver, **kw):
    return Event(rel=rel, ord=version_ord(ver), version=ver, **kw)


O = version_ord


def test_validity_rule_all_statuses():
    evs = [ev(ADDED_IN, "1.0", sig="(a)"), ev(REMOVED_IN, "2.0"), ev(ADDED_IN, "3.0", sig="(a, b)")]
    assert verdict_at(evs, O("0.9")).status == "added_later"
    assert verdict_at(evs, O("0.9")).deciding.version == "1.0"
    assert verdict_at(evs, O("1.5")).status == "valid"
    assert verdict_at(evs, O("2.0")).status == "removed"
    assert verdict_at(evs, O("2.5")).deciding.version == "2.0"
    v3 = verdict_at(evs, O("3.1"))
    assert v3.status == "valid" and v3.signature == "(a, b)"
    assert verdict_at([], O("1.0")).status == "missing"


def test_deprecated_and_signature_at():
    evs = [ev(ADDED_IN, "1.0", sig="(a)"), ev(CHANGED_IN, "1.2", kind="signature", before="(a)", after="(a, b=1)"),
           ev(CHANGED_IN, "1.4", kind="deprecated", after="use g instead")]
    assert signature_at(evs, O("1.1")) == "(a)"
    assert signature_at(evs, O("1.3")) == "(a, b=1)"
    v = verdict_at(evs, O("1.5"))
    assert v.status == "deprecated" and v.usable and v.deprecation.after == "use g instead"
    assert verdict_at(evs, O("1.3")).status == "valid"
    # a re-add after the deprecation clears it
    evs2 = evs + [ev(REMOVED_IN, "2.0"), ev(ADDED_IN, "2.1", sig="(a)")]
    assert verdict_at(evs2, O("2.2")).status == "valid"


def test_parse_params():
    p = parse_params("(self, a, b: int = 2, *args, c=(1, 2), d='x,y', **kw) -> 'R'")
    assert p.names == ("a", "b", "c", "d") and p.var_keyword and p.accepts("anything")
    q = parse_params("read_csv(path, /, sep=',', *, squeeze: bool = False)")
    assert q.names == ("path", "sep", "squeeze") and not q.accepts("path") and q.accepts("squeeze")
    assert not q.accepts("nope")
    assert parse_params("").accepts("x")  # unknown signature never raises false alarms


def test_keyword_fix_both_directions():
    evs = [ev(ADDED_IN, "1.0", sig="(a, squeeze=False)"),
           ev(CHANGED_IN, "2.0", kind="signature", before="(a, squeeze=False)", after="(a, squeeze_me=False)")]
    new = parse_params("(a, squeeze_me=False)")
    fix = keyword_fix("squeeze", evs, new)
    assert fix.replacement == "squeeze_me" and fix.event.version == "2.0"
    old = parse_params("(a, squeeze=False)")
    assert keyword_fix("squeeze_me", evs, old).replacement == "squeeze"
    assert keyword_fix("other", evs, new).replacement == ""


def test_words_and_dotted():
    w = words("Use DataFrame.append to add the rows; compute read_csv with 3 columns of a fft")
    assert "dataframe" in w and "data" in w and "frame" in w and "append" in w
    assert "read_csv" in w and "read" in w and "csv" in w and "fft" in w
    assert "the" not in w and "3" not in w and "a" not in w
    assert split_identifier("scipy.special.i1") == ["scipy", "special", "i1"]
    assert dotted_names("call np.linalg.norm or DataFrame.append, v1.2.3") == ["np.linalg.norm", "DataFrame.append"]


def test_read_code_aliases_and_types():
    code = """
import numpy as np
import scipy.special
import scipy.linalg as sl
from scipy import special as sp
from scipy.special import i1 as bessel, gamma
import os

def f(df: "np.ndarray", x):
    '''Compute the bessel function.'''
    # modified bessel of order one
    a = np.array([1, 2]).reshape(2, 1)
    b = np.linalg.norm(a, ord=2)
    c = scipy.special.i0(x) + sp.i1e(x) + bessel(x) + gamma(x)
    d = sl.inv(a)
    m = Matrix(3)
    obj.resample(rule="1D")
    df.sum()
    return np.float64
"""
    r = read_code(code, {"numpy", "scipy"})
    got = {(x.path, x.kind) for x in r.refs}
    assert ("numpy.array", "call") in got
    assert ("numpy.array.reshape", "call") not in got   # np.array is a function, not a class: no instance type
    assert ("numpy.linalg.norm", "call") in got
    assert ("scipy.special.i0", "call") in got and ("scipy.special.i1e", "call") in got
    assert ("scipy.special.i1", "call") in got and ("scipy.special.gamma", "call") in got
    assert ("scipy.special.i1", "import") in got and ("scipy.special", "import") in got
    assert ("scipy.linalg.inv", "call") in got and ("numpy.float64", "attr") in got
    assert ("numpy.ndarray.sum", "call") in got  # string annotation -> instance type
    assert not any(p.startswith("os") for p, _ in got)
    norm = next(x for x in r.refs if x.path == "numpy.linalg.norm")
    assert norm.keywords == ("ord",) and norm.line == 13
    assert "resample" in r.loose
    assert "bessel" in r.words and "modified" in r.words
    assert r.aliases["np"] == "numpy" and r.aliases["bessel"] == "scipy.special.i1"


def test_read_code_instances_and_errors():
    code = "import pandas as pd\ndf = pd.DataFrame({})\ndf.append(1)\npd.DataFrame().append(2, ignore_index=True)\n"
    r = read_code(code, {"pandas"})
    apps = [x for x in r.refs if x.path == "pandas.DataFrame.append"]
    assert [x.line for x in apps] == [3, 4] and all(x.inferred for x in apps)
    assert apps[1].keywords == ("ignore_index",)
    bad = read_code("def f(:\n  pass", {"pandas"})
    assert bad.error and bad.error_line == 1 and bad.refs == []
