"""maintain(): path compression, entity merge, entity split, decay — on the real graph."""

from pinned.contract import ALIAS_OF, CHANGED_IN, CUES, SHORTCUT, W_FLOOR, Context, ContextItem
from pinned.graph import edges_from, link_props_many, version_ord
from pinned.learn import GraphLearner
from pinned.learn.weights import ALPHA, W_DECAYED

from .conftest import build_world


def _learner(world, tmp_path):
    return GraphLearner(world.graph, run_id=world.run_id, log_dir=tmp_path)


def _code(world, call="sub.new_fn(x)"):
    return f"import {world.mod}\n\ndef solve(x):\n    return {world.mod}.{call}\n"


def _chain_pass(world, L, pid, version, passed=True):
    return L.record(world.episode(pid, version, _code(world), passed, problem=f"the {world.words['bessel']} values",
                                  context=world.chain_ctx(version),
                                  error_type="" if passed else "AssertionError", error_text="" if passed else "no"))


def test_shortcut_only_after_two_passes_and_only_for_its_range(world, tmp_path):
    L = _learner(world, tmp_path)
    t, a = world.term("bessel"), world.api("sub.new_fn")
    _chain_pass(world, L, "p1", "2.0")
    rep = L.maintain()
    assert rep.shortcuts_added == 0 and world.edge(SHORTCUT, t, a) is None  # one pass is not proof

    _chain_pass(world, L, "p2", "3.0")
    rep = L.maintain()
    assert rep.shortcuts_added == 1
    sc = world.edge(SHORTCUT, t, a, ("w", "lo_ord", "hi_ord", "hops"))
    assert sc == {"w": 1.0 + ALPHA, "lo_ord": version_ord("2.0"), "hi_ord": version_ord("3.0"), "hops": 3}
    # only from the word that cued the chain's start, never from other problem words
    assert [r["dst"] for r in edges_from(world.graph, [t], SHORTCUT)] == [a]
    moves = [m for m in L.log.read() if m["move"] == "shortcut"]
    assert len(moves) == 1 and sorted(moves[0]["episodes"]) == [f"{world.run_id}|p1|1", f"{world.run_id}|p2|1"]
    # running maintain again changes nothing
    assert L.maintain().shortcuts_added == 0


def test_shortcut_not_added_when_chain_fails_as_often(world, tmp_path):
    L = _learner(world, tmp_path)
    _chain_pass(world, L, "p1", "2.0")
    _chain_pass(world, L, "p2", "2.0")
    _chain_pass(world, L, "f1", "3.0", passed=False)
    _chain_pass(world, L, "f2", "3.0", passed=False)
    assert L.maintain().shortcuts_added == 0
    assert world.edge(SHORTCUT, world.term("bessel"), world.api("sub.new_fn")) is None


def test_entity_merge_alias_of(world, tmp_path):
    L = _learner(world, tmp_path)
    _chain_pass(world, L, "p1", "2.0")  # maintain only looks at libraries this run has episodes for
    rep = L.maintain()
    assert rep.merged == 2
    assert [r["dst"] for r in edges_from(world.graph, [world.api("helper")], ALIAS_OF)] == [world.api("tools.helper")]
    assert [r["dst"] for r in edges_from(world.graph, [world.api("p.solve")], ALIAS_OF)] == [world.api("q.solve")]
    assert edges_from(world.graph, [world.api("x.compute"), world.api("y.compute")], ALIAS_OF) == []  # no proof
    # existing ALIAS_OF (mid_fn -> new_fn) untouched; second pass adds nothing
    assert L.maintain().merged == 0


def test_entity_split_changed_in_per_version(world, tmp_path):
    L = _learner(world, tmp_path)
    ok = _code(world, "fit(x, normalize=True)")
    L.record(world.episode("s1", "1.0", ok, True, problem=world.words["fit"]))
    L.record(world.episode("s2", "2.0", ok, False, problem=world.words["fit"], error_type="TypeError",
                           error_text="Traceback...\nTypeError: fit() got an unexpected keyword argument 'normalize'"))
    rep = L.maintain()
    assert rep.split == 1
    rows = edges_from(world.graph, [world.api("fit")], CHANGED_IN, props=("kind", "before", "after", "evidence"),
                      dst_props=("version",))
    assert len(rows) == 1
    r = rows[0]
    assert r["dst_version"] == "2.0" and r["kind"] == "behaviour"
    assert r["before"] == f"works at {world.lib}==1.0"
    assert "unexpected keyword argument 'normalize'" in r["after"] and r["evidence"].startswith("learned from episodes")
    assert L.maintain().split == 0  # the graph now answers per version


def test_entity_split_uses_real_signatures_when_known(graph, run_key, tmp_path):
    world = build_world(graph, run_key)
    # graph knows a different signature at 3.0 (re-added) but has no CHANGED_IN between 1.0 and 3.0
    link_props_many(graph, "ADDED_IN", [(world.api("concat"), world.ver("3.0"), {"sig": "(objs)", "evidence": "fx"})])
    L = _learner(world, tmp_path)
    code = _code(world, "concat([1], axis=0)")
    L.record(world.episode("s1", "1.0", code, True))
    L.record(world.episode("s2", "3.0", code, False, error_type="TypeError",
                           error_text="concat() got an unexpected keyword argument 'axis'"))
    assert L.maintain().split == 1
    r = edges_from(graph, [world.api("concat")], CHANGED_IN, props=("kind", "before", "after"))[0]
    assert (r["kind"], r["before"], r["after"]) == ("signature", "(objs, axis=0)", "(objs)")


def test_decay_drops_failing_edges_below_floor(world, tmp_path):
    L = _learner(world, tmp_path)
    t, a = world.term("concat"), world.api("concat")
    link_props_many(world.graph, CUES, [(t, a, {"w": 2.0})])  # a strong edge that starts failing
    ctx = Context(world.lib, "2.0", [ContextItem(world.lib, world.p("concat"), "valid")])
    for i in range(3):
        L.record(world.episode(f"f{i}", "2.0", _code(world, "concat([1])"), False, problem=world.words["concat"],
                               context=ctx, error_type="AssertionError", error_text="wrong output"))
    before = world.edge(CUES, t, a)
    assert before["w"] >= W_FLOOR and before["n_fail"] == 3
    rep = L.maintain()
    assert rep.edges_reweighted >= 1
    assert world.edge(CUES, t, a)["w"] == W_DECAYED < W_FLOOR
    assert any(m["move"] == "decay" and m["keys"]["dst"] == f"{world.lib}|{world.p('concat')}" for m in L.log.read())


def test_maintain_report_counts_and_log(world, tmp_path):
    L = _learner(world, tmp_path)
    _chain_pass(world, L, "p1", "2.0")
    _chain_pass(world, L, "p2", "2.0")
    rep = L.maintain()
    assert (rep.shortcuts_added, rep.merged) == (1, 2)
    assert rep.notes and "shortcuts=1" in rep.notes[0] and "merged=2" in rep.notes[0]
    moves = [m["move"] for m in L.log.read()]
    assert moves.count("shortcut") == 1 and moves.count("merge") == 2
