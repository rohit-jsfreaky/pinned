"""GraphSearch against a real HydraDB node, on fixture graphs built in exactly the contract.py schema."""

from pinned.contract import ADDED_IN, ALIAS_OF, CHANGED_IN, CUES, REMOVED_IN, REPLACED_BY, SHORTCUT, TERM, W_FLOOR, Hop
from pinned.graph import node_id, set_edges_many


def rels(chain):
    return [(h.src, h.rel, h.dst) for h in chain]


def item(ctx, path):
    return next(i for i in ctx.items if i.path == path)


# ---------------------------------------------------------------------------------------------------- chains
def test_removed_then_replaced_multi_hop_through_alias(lib, search):
    b = lib()
    m = b.imp
    b.api("old", added=[("1.0.0", "(x)")], removed=["2.0.0"])
    b.api("mid", added=[("1.0.0", "(x)")], removed=["1.1.0"])          # also gone by 2.0
    b.api("core.new", added=[("1.1.0", "(x, axis=0)")])
    b.replace("old", "mid").alias("mid", "core.new").commit()

    ctx = search.context_for("call it", f"import {m}\n{m}.old(1)\n", b.name, "2.1.0", k=8)
    old = item(ctx, f"{m}.old")
    assert old.status == "removed" and old.signature == ""
    assert rels(old.chain) == [
        (f"{m}.old", REMOVED_IN, f"{b.name} 2.0.0"),
        (f"{m}.old", REPLACED_BY, f"{m}.mid"),
        (f"{m}.mid", ALIAS_OF, f"{m}.core.new"),
        (f"{m}.core.new", ADDED_IN, f"{b.name} 1.1.0"),
    ]
    assert f"Use {m}.core.new instead" in old.note
    new = item(ctx, f"{m}.core.new")
    assert new.status == "valid" and new.signature == "(x, axis=0)"
    assert ctx.graph_queries >= 3 and ctx.query_ms > 0
    assert "REMOVED_IN" in ctx.to_prompt()

    # before the removal the same call is simply valid, with no walk
    ctx1 = search.context_for("call it", f"import {m}\n{m}.old(1)\n", b.name, "1.1.0")
    assert item(ctx1, f"{m}.old").status == "valid" and item(ctx1, f"{m}.old").signature == "(x)"

    f = [x for x in search.check_calls(f"from {m} import old\nold(2)\n", b.name, "3.0.0") if x.line == 2][0]
    assert f.call == f"{m}.old" and f.status == "removed" and f.fix == f"{m}.core.new"


def test_alias_rename_status(lib, search):
    b = lib()
    m = b.imp
    b.api("io.read", added=[("1.0.0", "(p)")], removed=["2.0.0"])
    b.api("read", added=[("2.0.0", "(p)")])
    b.alias("io.read", "read").commit()
    [f] = [x for x in search.check_calls(f"import {m}.io\n{m}.io.read('a')\n", b.name, "2.0.0") if x.line == 2]
    assert f.status == "renamed" and f.fix == f"{m}.read"
    assert (f"{m}.io.read", ALIAS_OF, f"{m}.read") in rels(f.chain)


def test_higher_w_replacement_preferred_and_floor_ignored(lib, search, graph):
    b = lib()
    m = b.imp
    b.api("old", added=[("1.0.0", "()")], removed=["2.0.0"])
    b.api("a", added=[("1.0.0", "()")])
    b.api("b", added=[("1.0.0", "()")])
    b.replace("old", "a", w=1.0).replace("old", "b", w=1.5).commit()
    code = f"import {m}\n{m}.old()\n"
    assert search.check_calls(code, b.name, "2.0.0")[0].fix == f"{m}.b"
    set_edges_many(graph, REPLACED_BY, [(b.aid("old"), b.aid("b"), {"w": W_FLOOR / 2})])   # decayed below the floor
    assert search.check_calls(code, b.name, "2.0.0")[0].fix == f"{m}.a"
    set_edges_many(graph, REPLACED_BY, [(b.aid("old"), b.aid("a"), {"w": 0.1})])
    f = search.check_calls(code, b.name, "2.0.0")[0]
    assert f.status == "removed" and f.fix == ""


# ---------------------------------------------------------------------------------------------------- versions
def test_signature_change_per_version_and_explain(lib, search):
    b = lib()
    m = b.imp
    b.api("Frame", kind="class", added=[("1.0.0", "(data)")])
    b.api("Frame.resample", kind="method", added=[("1.0.0", "(rule, how=None)")],
          changed=[("2.0.0", "signature", "(rule, how=None)", "(rule)"),
                   ("2.1.0", "deprecated", "", "use Frame.window instead")])
    b.api("Frame.window", kind="method", added=[("2.0.0", "(rule)")])
    b.replace("Frame.resample", "Frame.window", evidence="use Frame.window instead").commit()
    code = f"import {m}\ndf = {m}.Frame([1])\ndf.resample('1D')\n"
    sig = {v: item(search.context_for("x", code, b.name, v), f"{m}.Frame.resample").signature for v in ("1.1.0", "2.0.0")}
    assert sig == {"1.1.0": "(rule, how=None)", "2.0.0": "(rule)"}

    dep = item(search.context_for("x", code, b.name, "3.0.0"), f"{m}.Frame.resample")
    assert dep.status == "deprecated" and dep.signature == "(rule)" and f"Prefer {m}.Frame.window" in dep.note
    assert (f"{m}.Frame.resample", CHANGED_IN, f"{b.name} 2.1.0") in rels(dep.chain)

    t = search.explain_api(b.name, f"{m}.Frame.resample")
    assert t.kind == "method"
    assert [(v.version, v.status, v.signature) for v in t.versions] == [
        ("1.0.0", "valid", "(rule, how=None)"), ("1.1.0", "valid", "(rule, how=None)"), ("2.0.0", "valid", "(rule)"),
        ("2.1.0", "deprecated", "(rule)"), ("3.0.0", "deprecated", "(rule)")]
    assert t.versions[0].note == "added" and "signature changed" in t.versions[2].note
    assert rels(t.replaced_by) == [(f"{m}.Frame.resample", REPLACED_BY, f"{m}.Frame.window")]
    assert t.graph_queries == 2


def test_remove_then_readd(lib, search):
    b = lib()
    m = b.imp
    b.api("f", added=[("1.0.0", "(a)"), ("2.1.0", "(a, b)")], removed=["1.1.0"]).commit()
    t = search.explain_api(b.name, f"{m}.f")
    assert [v.status for v in t.versions] == ["valid", "removed", "removed", "valid", "valid"]
    assert t.versions[3].signature == "(a, b)"
    statuses = {v: search.check_calls(f"import {m}\n{m}.f(1)\n", b.name, v)[0].status for v in ("1.0.0", "2.0.0", "3.0.0")}
    assert statuses == {"1.0.0": "valid", "2.0.0": "removed", "3.0.0": "valid"}


def test_added_later_walks_back_to_the_older_api(lib, search):
    b = lib()
    m = b.imp
    b.api("integrate.trapz", added=[("1.0.0", "(y, x=None)")], changed=[("2.1.0", "deprecated", "", "use trapezoid")])
    b.api("integrate.trapezoid", added=[("2.0.0", "(y, x=None)")])
    b.replace("integrate.trapz", "integrate.trapezoid", evidence="use trapezoid").commit()
    [f] = search.check_calls(f"from {m}.integrate import trapezoid\ntrapezoid([1])\n", b.name, "1.1.0")[1:]
    assert f.status == "added_later" and f.fix == f"{m}.integrate.trapz"
    assert rels(f.chain)[0] == (f"{m}.integrate.trapezoid", ADDED_IN, f"{b.name} 2.0.0")
    assert (f"{m}.integrate.trapz", REPLACED_BY, f"{m}.integrate.trapezoid") in rels(f.chain)
    ctx = search.context_for("integrate", f"import {m}\n{m}.integrate.trapezoid([1])", b.name, "1.1.0")
    assert item(ctx, f"{m}.integrate.trapezoid").status == "added_later"
    assert item(ctx, f"{m}.integrate.trapz").status == "valid"
    # at 3.0 trapz is deprecated and the newer API is the advice
    d = search.check_calls(f"from {m}.integrate import trapz\ntrapz([1])\n", b.name, "3.0.0")[1]
    assert d.status == "deprecated" and d.fix == f"{m}.integrate.trapezoid"


def test_missing_api(lib, search):
    b = lib()
    m = b.imp
    b.api("real", added=[("1.0.0", "()")]).commit()
    fs = search.check_calls(f"import {m}\n{m}.real()\n{m}.ghost()\n", b.name, "1.0.0")
    assert [(f.call, f.status) for f in fs] == [(f"{m}.real", "valid"), (f"{m}.ghost", "missing")]
    ctx = search.context_for("", f"import {m}\n{m}.ghost()\n", b.name, "1.0.0")
    assert item(ctx, f"{m}.ghost").status == "missing"


# ---------------------------------------------------------------------------------------------------- check_calls
def test_keyword_argument_changes(lib, search):
    b = lib()
    m = b.imp
    b.api("io.loadmat", added=[("1.0.0", "(file, squeeze=False, **kwargs)")],
          changed=[("2.0.0", "signature", "(file, squeeze=False, **kwargs)", "(file, *, squeeze_me=False)")])
    b.commit()
    code = f"from {m} import io as sio\nsio.loadmat('f', squeeze=True)\nsio.loadmat('f', squeeze_me=True, bogus=1)\n"
    new = search.check_calls(code, b.name, "3.0.0")
    kw = [(f.call, f.status, f.fix, f.line) for f in new if f.status == "changed"]
    assert kw[0] == (f"{m}.io.loadmat(squeeze=...)", "changed", "use squeeze_me= instead of squeeze= "
                     f"({b.name} 3.0.0)", 2)
    assert kw[1][0] == f"{m}.io.loadmat(bogus=...)" and kw[1][2].startswith("remove bogus=") and kw[1][3] == 3
    assert len(kw) == 2
    assert [f for f in new if f.call == f"{m}.io.loadmat(squeeze=...)"][0].chain[0].rel == CHANGED_IN
    old = search.check_calls(code, b.name, "1.1.0")   # **kwargs at 1.x accepts any keyword: no false alarm
    assert [f.status for f in old if f.line > 1] == ["valid", "valid"]
    b2 = lib(("1.0.0", "2.0.0"))
    b2.api("io.save", added=[("1.0.0", "(file, compress=False)")],
           changed=[("2.0.0", "signature", "(file, compress=False)", "(file, zip=False)")]).commit()
    back = search.check_calls(f"from {m}.io import save\nsave('f', zip=True)\n", b2.name, "1.0.0")
    assert [f.fix for f in back if f.status == "changed"] == [f"use compress= instead of zip= ({b2.name} 1.0.0)"]


def test_import_alias_resolution(lib, search):
    b = lib()
    m = b.imp
    for p in ("special.i1", "special.gamma", "linalg.norm"):
        b.api(p, added=[("1.0.0", "(x)")])
    b.commit()
    code = (f"import {m} as q\nfrom {m} import special\nfrom {m}.special import gamma as G\nimport {m}.linalg\n"
            f"q.special.i1(1); special.i1(2); G(3); {m}.linalg.norm(4)\n")
    fs = [(f.call, f.status) for f in search.check_calls(code, b.name, "2.0.0") if f.line == 5]
    assert sorted(fs) == sorted([(f"{m}.special.i1", "valid"), (f"{m}.special.gamma", "valid"),
                                 (f"{m}.linalg.norm", "valid")])


def test_code_that_does_not_parse(lib, search):
    b = lib()
    b.api("f", added=[("1.0.0", "()")]).commit()
    [f] = search.check_calls("def broken(:\n", b.name, "1.0.0")
    assert f.status == "unknown" and "does not parse" in f.fix and f.line == 1


# ---------------------------------------------------------------------------------------------------- ranking
def test_ranking_follows_learned_cues_weights(lib, search, graph):
    b = lib()
    m = b.imp
    b.api("smooth", added=[("1.0.0", "(x)")]).api("blur", added=[("1.0.0", "(x)")])
    b.cue("soften", "smooth", w=1.0).cue("soften", "blur", w=0.8).commit()
    order = [i.path for i in search.context_for("soften the image", "", b.name, "1.0.0").items]
    assert order[:2] == [f"{m}.smooth", f"{m}.blur"]
    set_edges_many(graph, CUES, [(node_id(TERM, "soften"), b.aid("blur"), {"w": 2.0})])   # learning raised it
    ctx = search.context_for("soften the image", "", b.name, "1.0.0")
    assert [i.path for i in ctx.items][:2] == [f"{m}.blur", f"{m}.smooth"]
    assert ctx.items[0].chain[0] == Hop("soften", CUES, f"{m}.blur", "w=2.00")


def test_common_words_count_less(lib, search):
    b = lib()
    m = b.imp
    for i in range(6):
        b.api(f"generic{i}", added=[("1.0.0", "()")]).cue("compute", f"generic{i}", w=1.0)
    b.api("bessel_i1", added=[("1.0.0", "()")]).cue("bessel", "bessel_i1", w=0.6).cue("compute", "bessel_i1")
    b.commit()
    ctx = search.context_for("compute the bessel thing", "", b.name, "1.0.0", k=3)
    assert ctx.items[0].path == f"{m}.bessel_i1"


def test_edges_below_floor_are_ignored(lib, search):
    b = lib()
    m = b.imp
    b.api("weak", added=[("1.0.0", "()")]).cue("frobnicate", "weak", w=W_FLOOR - 0.01).commit()
    ctx = search.context_for("frobnicate", "", b.name, "1.0.0")
    assert all(i.path != f"{m}.weak" for i in ctx.items)


def test_shortcut_applies_only_inside_its_range(lib, search):
    b = lib()
    m = b.imp
    b.api("rare", added=[("1.0.0", "()")]).api("other", added=[("1.0.0", "()")])
    b.cue("quux", "other", w=1.0).shortcut("quux", "rare", lo="1.1.0", hi="2.0.0", w=1.0).commit()
    top = {v: search.context_for("quux", "", b.name, v).items[0] for v in ("1.0.0", "1.1.0", "2.0.0", "2.1.0")}
    assert {v: i.path for v, i in top.items()} == {"1.0.0": f"{m}.other", "1.1.0": f"{m}.rare",
                                                  "2.0.0": f"{m}.rare", "2.1.0": f"{m}.other"}
    assert top["1.1.0"].chain[0].rel == SHORTCUT
    assert all(i.path != f"{m}.rare" for i in search.context_for("quux", "", b.name, "3.0.0").items)


def test_task_text_names_and_member_boost(lib, search):
    b = lib()
    m = b.imp
    b.api("Frame", kind="class", added=[("1.0.0", "(data)")])
    b.api("Frame.append", kind="method", added=[("1.0.0", "(other)")], removed=["2.0.0"])
    b.api("concat", added=[("1.0.0", "(objs)")])
    b.api("Series.append", kind="method", added=[("1.0.0", "(other)")])
    b.replace("Frame.append", "concat", evidence="use concat instead")
    b.cue("append", "Frame.append").cue("append", "Series.append").commit()
    # the text names Frame.append without the import name; the graph knows it
    ctx = search.context_for("Add a row like Frame.append did", f"import {m} as pd\n", b.name, "2.0.0", k=4)
    first = ctx.items[0]
    assert first.path == f"{m}.Frame.append" and first.status == "removed"
    assert ctx.items[1].path == f"{m}.concat" and ctx.items[1].status == "valid"
    # member boost: code uses the class, so its method outranks the other class's method of the same word
    ctx2 = search.context_for("append things", f"import {m} as pd\nx = pd.Frame([1])\n", b.name, "1.0.0")
    order = [i.path for i in ctx2.items]
    assert order.index(f"{m}.Frame.append") < order.index(f"{m}.Series.append")


# ---------------------------------------------------------------------------------------------------- unknowns + counters
def test_unknown_library_and_version(lib, search, run_key):
    b = lib()
    b.api("f", added=[("1.0.0", "()")]).commit()
    for libname, ver in ((f"nolib{run_key}", "1.0.0"), (b.name, "9.9.9"), (b.name, "not-a-version"), ("", "1.0")):
        ctx = search.context_for("anything", "import os", libname, ver)
        assert len(ctx.items) == 1 and ctx.items[0].status == "unknown" and ctx.items[0].note
        [f] = search.check_calls("x = 1", libname, ver)
        assert f.status == "unknown" and f.fix
    assert "Ingested versions: 1.0.0" in search.context_for("", "", b.name, "9.9.9").items[0].note
    t = search.explain_api(f"nolib{run_key}", "x.y")
    assert t.versions == [] and t.replaced_by == []
    t2 = search.explain_api(b.name, f"{b.imp}.nothing")
    assert [v.status for v in t2.versions] == ["missing"] * 5


def test_version_matched_by_ord(lib, search):
    b = lib()
    b.api("f", added=[("1.0.0", "()")]).commit()
    ctx = search.context_for("", f"import {b.imp}\n{b.imp}.f()", b.name, "1.0")
    assert ctx.version == "1.0.0" and ctx.items[0].status == "valid"


def test_graph_queries_matches_client_counter(lib, search, graph):
    b = lib()
    m = b.imp
    b.api("old", added=[("1.0.0", "()")], removed=["2.0.0"]).api("new", added=[("1.0.0", "()")])
    b.replace("old", "new").cue("thing", "old").commit()
    before = graph.requests
    ctx = search.context_for("thing", f"import {m}\n{m}.old()\n", b.name, "2.0.0")
    assert ctx.graph_queries == graph.requests - before
    assert ctx.graph_queries == 4   # meta, cues+shortcuts, events, one SSpaths walk


def test_context_skips_imports_the_code_uses(lib, search):
    b = lib()
    m = b.imp
    b.api("special", kind="module", added=[("1.0.0", "")]).api("special.i1", added=[("1.0.0", "(x)")]).commit()
    ctx = search.context_for("bessel", f"from {m} import special\nspecial.i1(1)\n", b.name, "1.0.0")
    assert [i.path for i in ctx.items] == [f"{m}.special.i1"]
    alone = search.context_for("bessel", f"from {m} import special\n", b.name, "1.0.0")
    assert [i.path for i in alone.items] == [f"{m}.special"]
