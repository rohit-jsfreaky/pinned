"""PlainStore (ablation: same data, plain dicts, no traversal) and ShuffledSearch (negative control)."""

import json

import pytest

from pinned.contract import CUES, SHORTCUT, ApiTimeline, Context, ContextItem, Finding
from pinned.graph import link_props_many, version_ord
from pinned.learn import GraphLearner, PlainStore, ShuffledSearch, TestSplitError
from pinned.learn.weights import ALPHA, BETA, W_DECAYED, W_LEARNED


class ExplodingGraph:
    """Any graph access fails: proves PlainStore answers from its dicts only."""

    def query(self, *a, **k):
        raise AssertionError("PlainStore touched the graph")

    def write(self, *a, **k):
        raise AssertionError("PlainStore touched the graph")


def _store(world, **kw):
    return PlainStore.from_graph(world.graph, world.lib, **kw)


def test_from_graph_holds_the_same_facts_and_signals(world):
    s = _store(world)
    d = s.libs[world.lib]
    assert d.import_names == [world.mod] and sorted(d.versions) == ["1.0", "2.0", "3.0"]
    assert world.p("old_fn") in s.valid_paths(world.lib, "1.0") and world.p("old_fn") not in s.valid_paths(world.lib, "2.0")
    assert world.p("sub.new_fn") not in s.valid_paths(world.lib, "1.0")
    assert world.p("sub.new_fn") in s.valid_paths(world.lib, "2.0")
    assert d.cues[world.words["bessel"]] == {world.p("old_fn"): 1.0}
    tl = s.explain_api(world.lib, world.p("old_fn"))
    assert [(v.version, v.status) for v in tl.versions] == [("1.0", "valid"), ("2.0", "removed"), ("3.0", "removed")]
    assert tl.replaced_by == []  # no chains in a plain store


def test_answers_from_dicts_only_and_never_traverses(world, monkeypatch):
    s = _store(world)
    boom = ExplodingGraph()
    monkeypatch.setattr(world.graph, "query", boom.query)  # from here on any graph read fails
    monkeypatch.setattr(world.graph, "write", boom.write)
    assert not any(isinstance(v, type(world.graph)) for v in vars(s).values())
    s_json = json.dumps(s.to_dict())  # the whole store is plain data
    assert "HydraGraph" not in s_json
    ctx = s.context_for(f"compute {world.words['bessel']} of x", "", world.lib, "2.0")
    paths = [it.path for it in ctx.items]
    assert paths == [world.p("old_fn")]
    it = ctx.items[0]
    assert it.status == "removed" and it.chain == []  # the graph would walk old_fn -> mid_fn -> new_fn; this cannot
    assert world.p("sub.new_fn") not in paths and world.p("mid_fn") not in paths
    s.check_calls(f"import {world.mod}\n{world.mod}.old_fn(1)", world.lib, "2.0")
    s.explain_api(world.lib, world.p("old_fn"))
    s.record(world.episode("p1", "1.0", f"import {world.mod}\n{world.mod}.old_fn(1)", True, problem=world.words["bessel"]))
    s.maintain()
    s2 = PlainStore.from_dict(s.to_dict())
    assert s2.context_for(f"{world.words['bessel']}", "", world.lib, "2.0").items[0].path == world.p("old_fn")


def test_check_calls_and_unknowns(world):
    s = _store(world)
    code = f"import {world.mod} as m\nm.old_fn(1)\nm.fit(1, normalize=True)\nm.concat([1], axis9=2)\n"
    f = {x.call: x for x in s.check_calls(code, world.lib, "2.0")}
    assert f[world.p("old_fn")].status == "removed" and f[world.p("old_fn")].fix == ""  # no replacement known
    assert f[world.p("fit")].status == "valid"
    assert f[world.p("concat")].status == "changed" and "axis9" in f[world.p("concat")].fix
    assert s.check_calls("def (:", world.lib, "2.0")[0].status == "unknown"
    assert s.check_calls("x", "nolib", "1.0") == [Finding(call="nolib", status="unknown",
                                                          fix="library 'nolib' is not in this store")]
    assert s.context_for("x", "", "nolib", "1.0").items == []
    assert s.context_for("x", "", world.lib, "not a version").items == []
    assert s.explain_api("nolib", "x") == ApiTimeline("nolib", "x")


def test_starting_code_and_learned_weights_change_ranking(world):
    s = _store(world)
    code = f"import {world.mod}\n{world.mod}.concat([1])\n"
    ctx = s.context_for(f"{world.words['bessel']} {world.words['fit']}", code, world.lib, "2.0", k=2)
    assert ctx.items[0].path == world.p("concat")  # code bonus
    problem = f"{world.words['fit']} {world.words['bessel']}"
    first = [it.path for it in s.context_for(problem, "", world.lib, "1.0").items]
    assert first == sorted([world.p("fit"), world.p("old_fn")])  # equal weights: path order
    s.record(world.episode("p1", "1.0", f"import {world.mod}\n{world.mod}.old_fn(1)", True, problem=problem))
    assert [it.path for it in s.context_for(problem, "", world.lib, "1.0").items][0] == world.p("old_fn")


def test_plain_learning_mirrors_graph_signals(world, tmp_path):
    s = _store(world)
    problem = f"merge rows {world.words['concat']} fast{world.k}"
    code = f"import {world.mod}\n{world.mod}.concat([1])\n"
    s.record(world.episode("f1", "2.0", code, False, problem=problem, error_type="AssertionError", error_text="x"))
    rep = s.record(world.episode("p1", "2.0", code, True, problem=problem))
    d = s.libs[world.lib]
    assert d.cues[world.words["concat"]][world.p("concat")] == pytest.approx(1.0 - BETA + ALPHA)
    assert d.cues[f"fast{world.k}"][world.p("concat")] == W_LEARNED
    assert any("learned cues added" in n for n in rep.notes)
    assert [e["passed"] for e in s.episodes] == [False, True]
    # the graph learner given the same episodes ends with the same weights on the same pair
    L = GraphLearner(world.graph, run_id=world.run_id, log_dir=tmp_path)
    L.record(world.episode("f1", "2.0", code, False, problem=problem, error_type="AssertionError", error_text="x"))
    L.record(world.episode("p1", "2.0", code, True, problem=problem))
    assert world.edge(CUES, world.term("concat"), world.api("concat"))["w"] == pytest.approx(1.0 - BETA + ALPHA)
    # re-recording is a no-op
    assert "already recorded" in s.record(world.episode("p1", "2.0", code, True, problem=problem)).notes[0]


def test_plain_split_guard_maintain_decay_and_roundtrip(world, tmp_path):
    split = tmp_path / "split.json"
    split.write_text(json.dumps({"seed": 13, "train": [], "test": ["te"]}))
    s = _store(world, split_path=split)
    code = f"import {world.mod}\n{world.mod}.fit(1)\n"
    with pytest.raises(TestSplitError):
        s.record(world.episode("te", "2.0", code, True, problem=world.words["fit"]))
    s.libs[world.lib].cues[world.words["fit"]][world.p("fit")] = 3.0
    for i in range(3):
        s.record(world.episode(f"f{i}", "2.0", code, False, problem=world.words["fit"], error_type="AssertionError"))
    assert s.libs[world.lib].cues[world.words["fit"]][world.p("fit")] >= 0.2
    rep = s.maintain()
    assert rep.edges_reweighted == 1 and s.libs[world.lib].cues[world.words["fit"]][world.p("fit")] == W_DECAYED
    path = tmp_path / "plain.json"
    s.save(path)
    s2 = PlainStore.load(path)
    assert s2.to_dict() == s.to_dict()
    with pytest.raises(ValueError):
        PlainStore.from_dict({"format": "other"})


def test_from_graph_folds_shortcuts_and_refresh_keeps_own_weights(world):
    t, a = world.term("bessel"), world.api("sub.new_fn")
    link_props_many(world.graph, SHORTCUT, [(t, a, {"w": 1.5, "lo_ord": version_ord("2.0"), "hi_ord": version_ord("3.0"),
                                                     "hops": 3})])
    s = _store(world)
    assert s.libs[world.lib].cues[world.words["bessel"]][world.p("sub.new_fn")] == 1.5
    s.libs[world.lib].cues[world.words["bessel"]][world.p("old_fn")] = 0.4  # its own learned weight
    link_props_many(world.graph, CUES, [(world.term("fit"), world.api("legacy"), {"w": 1.0, "source": "doc"})])
    s.refresh_facts(world.graph)
    cues = s.libs[world.lib].cues
    assert cues[world.words["bessel"]][world.p("old_fn")] == 0.4
    assert cues[world.words["fit"]][world.p("legacy")] == 1.0  # new ingested cue arrives


# ---------------------------------------------------------------------- ShuffledSearch
class FixedSearch:
    def __init__(self, n=6):
        self.items = [ContextItem("lib", f"m.f{i}", "valid", score=float(n - i)) for i in range(n)]

    def context_for(self, problem, starting_code, library, version, k=8):
        return Context(library, version, [ContextItem(**vars(it)) for it in self.items[:k]], query_ms=1.0, graph_queries=2)

    def check_calls(self, code, library, version):
        return [Finding("m.f0", "valid")]

    def explain_api(self, library, path):
        return ApiTimeline(library, path)


def test_shuffled_weights_keeps_size_breaks_ranking():
    inner = FixedSearch()
    sh = ShuffledSearch(inner, seed=7, mode="weights")
    ctx = sh.context_for("task", "", "lib", "1.0")
    base = inner.context_for("task", "", "lib", "1.0")
    assert len(ctx.items) == len(base.items) and {i.path for i in ctx.items} == {i.path for i in base.items}
    assert sorted(i.score for i in ctx.items) == sorted(i.score for i in base.items)
    assert [i.path for i in ctx.items] != [i.path for i in base.items]  # ranking broken
    assert [i.score for i in ctx.items] == sorted((i.score for i in ctx.items), reverse=True)
    assert ctx.query_ms == 1.0 and ctx.graph_queries == 2
    again = sh.context_for("task", "", "lib", "1.0")
    assert [i.path for i in again.items] == [i.path for i in ctx.items]  # reproducible per seed + problem
    other = ShuffledSearch(inner, seed=8, mode="weights").context_for("task", "", "lib", "1.0")
    assert [i.path for i in other.items] != [i.path for i in ctx.items] or len(ctx.items) < 3


def test_shuffled_items_random_valid_items_same_size():
    inner = FixedSearch(4)
    universe = [f"m.u{i}" for i in range(50)]
    sh = ShuffledSearch(inner, seed=1, universe=lambda lib, ver: universe)
    ctx = sh.context_for("task", "", "lib", "1.0")
    assert len(ctx.items) == 4 and all(i.path in universe for i in ctx.items) and all(i.status == "valid" for i in ctx.items)
    # without a universe the pool is what the inner search returned so far; too small -> filled with inner items
    sh2 = ShuffledSearch(inner, seed=1)
    ctx2 = sh2.context_for("task", "", "lib", "1.0")
    assert len(ctx2.items) == 4 and {i.path for i in ctx2.items} == {i.path for i in inner.items}
    assert sh2.check_calls("x", "lib", "1.0") == [Finding("m.f0", "valid")]
    assert sh2.explain_api("lib", "m.f0").path == "m.f0"
    with pytest.raises(ValueError):
        ShuffledSearch(inner, seed=1, mode="nope")


def test_shuffled_over_plain_store_universe(world):
    s = _store(world)
    sh = ShuffledSearch(s, seed=3, universe=s.valid_paths)
    base = s.context_for(f"{world.words['bessel']} {world.words['fit']}", "", world.lib, "2.0")
    ctx = sh.context_for(f"{world.words['bessel']} {world.words['fit']}", "", world.lib, "2.0")
    assert len(ctx.items) == len(base.items) == 2
    assert all(i.path in s.valid_paths(world.lib, "2.0") for i in ctx.items)
    assert {i.path for i in ctx.items}.isdisjoint({i.path for i in base.items})
