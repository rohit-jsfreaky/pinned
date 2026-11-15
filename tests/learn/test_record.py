"""record(): every move changes the real HydraDB graph exactly as specified (fixture graph in conftest.py)."""

import json

import pytest

from pinned.contract import CUES, EPISODE, REPLACED_BY, SHORTCUT, USED, W_FLOOR, Context, ContextItem, Hop
from pinned.graph import edges_from, link_props_many, node, node_id, version_ord
from pinned.learn import GraphLearner, TestSplitError
from pinned.learn.weights import ALPHA, BETA, W_LEARNED

from .conftest import FakeIngestor


def _learner(world, tmp_path, ingestor=None, split=None):
    return GraphLearner(world.graph, ingestor=ingestor, run_id=world.run_id, split_path=split, log_dir=tmp_path)


def _code(world, call="sub.new_fn(x)"):
    return f"import {world.mod}\n\ndef solve(x):\n    return {world.mod}.{call}\n"


def test_pass_raises_cues_and_chain_edges(world, tmp_path):
    L = _learner(world, tmp_path)
    ep = world.episode("p1", "2.0", _code(world), True, problem=f"modified {world.words['bessel']} of x",
                       context=world.chain_ctx("2.0"))
    # the context item new_fn came via bessel -> old_fn -> mid_fn -> new_fn; give bessel a direct cue to new_fn too
    link_props_many(world.graph, CUES, [(world.term("bessel"), world.api("sub.new_fn"), {"w": 1.0, "source": "doc"})])
    rep = L.record(ep)
    e = world.edge(CUES, world.term("bessel"), world.api("sub.new_fn"))
    assert e["w"] == 1.0 + ALPHA and e["n_pass"] == 1 and e["n_fail"] == 0 and e["source"] == "doc"
    r = world.edge(REPLACED_BY, world.api("old_fn"), world.api("mid_fn"))
    assert r["w"] == 1.0 + ALPHA and r["n_pass"] == 1
    assert world.edge(CUES, world.term("bessel"), world.api("old_fn"))["w"] == 1.0  # not used by the code: unchanged
    assert rep.edges_reweighted == 2


def test_fail_lowers_the_same_edges(world, tmp_path):
    L = _learner(world, tmp_path)
    ep = world.episode("p1", "2.0", _code(world), False, problem=f"{world.words['bessel']}",
                       context=world.chain_ctx("2.0"), error_type="AssertionError", error_text="assert 1 == 2")
    rep = L.record(ep)
    r = world.edge(REPLACED_BY, world.api("old_fn"), world.api("mid_fn"))
    assert r["w"] == pytest.approx(1.0 - BETA) and r["n_fail"] == 1 and r["n_pass"] == 0
    assert rep.edges_reweighted == 1 and rep.gaps_ingested == 0


def test_shortcut_reweighted_only_inside_its_range(world, tmp_path):
    L = _learner(world, tmp_path)
    t, a = world.term("bessel"), world.api("sub.new_fn")
    link_props_many(world.graph, SHORTCUT, [(t, a, {"w": 1.0, "lo_ord": version_ord("2.0"), "hi_ord": version_ord("2.0"),
                                                     "hops": 3})])
    ctx3 = Context(world.lib, "3.0", [ContextItem(world.lib, world.p("sub.new_fn"), "valid")])
    L.record(world.episode("p1", "3.0", _code(world), True, problem=world.words["bessel"], context=ctx3))
    assert world.edge(SHORTCUT, t, a)["w"] == 1.0  # 3.0 is outside [2.0, 2.0]
    ctx2 = Context(world.lib, "2.0", [ContextItem(world.lib, world.p("sub.new_fn"), "valid")])
    L.record(world.episode("p2", "2.0", _code(world), True, problem=world.words["bessel"], context=ctx2))
    assert world.edge(SHORTCUT, t, a)["w"] == 1.0 + ALPHA


def test_learned_cues_on_pass_only(world, tmp_path):
    L = _learner(world, tmp_path)
    problem = f"join two tables with {world.words['concat']} along rows quickly{world.k}"
    code = _code(world, "concat([a, b])")
    L.record(world.episode("f1", "2.0", code, False, problem=problem, error_type="AssertionError", error_text="x"))
    assert world.edge(CUES, node_id("Term", f"quickly{world.k}"), world.api("concat")) is None
    rep = L.record(world.episode("p1", "2.0", code, True, problem=problem))
    new = world.edge(CUES, node_id("Term", f"quickly{world.k}"), world.api("concat"))
    assert new == {"w": W_LEARNED, "n_pass": 1, "n_fail": 0, "source": "learned"}
    assert node(world.graph, "Term", node_id("Term", f"quickly{world.k}"), ("word",)) == {"word": f"quickly{world.k}"}
    old = world.edge(CUES, world.term("concat"), world.api("concat"))
    # first the failure (no context -> code APIs) lowered it, then the pass raised it; source stays "name"
    assert old["source"] == "name" and old["w"] == pytest.approx(1.0 - BETA + ALPHA)
    assert any(n.startswith("learned cues added") for n in rep.notes)


def test_test_split_episode_refused(world, tmp_path):
    split = tmp_path / "split.json"
    split.write_text(json.dumps({"seed": 13, "train": ["tr1"], "test": ["te1"]}))
    L = _learner(world, tmp_path, split=split)
    with pytest.raises(TestSplitError):
        L.record(world.episode("te1", "2.0", _code(world), True, problem=world.words["bessel"],
                               context=world.chain_ctx("2.0")))
    assert world.edge(REPLACED_BY, world.api("old_fn"), world.api("mid_fn"))["w"] == 1.0
    assert node(world.graph, EPISODE, node_id(EPISODE, f"{world.run_id}|te1|1")) is None
    assert L.log.read() == []
    L.record(world.episode("tr1", "2.0", _code(world), True, problem=world.words["bessel"], context=world.chain_ctx("2.0")))
    assert world.edge(REPLACED_BY, world.api("old_fn"), world.api("mid_fn"))["w"] == 1.0 + ALPHA


@pytest.mark.parametrize(
    "version, call, error_type, error_text, hint",
    [
        # graph says Frame.append is valid at 1.0, runtime says it is missing -> wrong facts
        ("1.0", "Frame(d).append(x)", "AttributeError", "'Frame' object has no attribute 'append'", "Frame.append"),
        # graph has no Api node at all for a module attribute
        ("2.0", "nothing(x)", "AttributeError", "module '{mod}' has no attribute 'nothing'", "nothing"),
        ("2.0", "x", "ImportError", "cannot import name 'gone' from '{mod}' (/site/{mod}/__init__.py)", "gone"),
        ("2.0", "x", "ModuleNotFoundError", "No module named '{mod}.extra'", "extra"),
        # graph signature at 2.0 still lists `normalize`, runtime rejects it -> wrong facts
        ("2.0", "fit(x, normalize=True)", "TypeError", "fit() got an unexpected keyword argument 'normalize'", "fit"),
        ("2.0", "legacy(1)", "", "w.py:1: DeprecationWarning: {mod}.legacy is deprecated, use {mod}.concat instead",
         "legacy"),
    ],
)
def test_gap_driven_ingestion_calls_fill_gap(world, tmp_path, version, call, error_type, error_text, hint):
    ing = FakeIngestor()
    L = _learner(world, tmp_path, ingestor=ing)
    rep = L.record(world.episode("g1", version, _code(world, call), False, error_type=error_type,
                                 error_text=error_text.format(mod=world.mod)))
    assert ing.calls == [(world.lib, version, world.p(hint))]
    assert rep.gaps_ingested == 1
    moves = [m for m in L.log.read() if m["move"] == "gap_fill"]
    assert moves[0]["keys"]["hint"] == world.p(hint) and moves[0]["after"]["added"] == 1


def test_gap_not_filled_when_graph_already_knew(world, tmp_path):
    ing = FakeIngestor()
    L = _learner(world, tmp_path, ingestor=ing)
    # graph already says Frame.append is removed at 2.0: the agent ignored the facts; nothing to ingest
    L.record(world.episode("g1", "2.0", _code(world, "Frame(d).append(x)"), False, error_type="AttributeError",
                           error_text="'Frame' object has no attribute 'append'"))
    # graph signature at 2.0 has no `axis2`: a correct fact
    L.record(world.episode("g2", "2.0", _code(world, "concat(x, axis2=1)"), False, error_type="TypeError",
                           error_text="concat() got an unexpected keyword argument 'axis2'"))
    assert ing.calls == []
    assert [m["move"] for m in L.log.read() if m["move"].startswith("gap")] == ["gap_known", "gap_known"]


def test_gap_failure_and_dedupe_and_no_ingestor(world, tmp_path):
    bad = FakeIngestor(fail=True)
    L = _learner(world, tmp_path, ingestor=bad)
    err = dict(error_type="AttributeError", error_text=f"module '{world.mod}' has no attribute 'nothing'")
    rep = L.record(world.episode("g1", "2.0", _code(world, "nothing()"), False, **err))
    assert rep.gaps_ingested == 0 and any("fill_gap failed" in n for n in rep.notes)
    assert node(world.graph, EPISODE, node_id(EPISODE, f"{world.run_id}|g1|1")) is not None  # episode still kept

    ok = FakeIngestor()
    L2 = _learner(world, tmp_path, ingestor=ok)
    L2.record(world.episode("g2", "2.0", _code(world, "nothing()"), False, **err))
    L2.record(world.episode("g3", "2.0", _code(world, "nothing()"), False, **err))
    assert len(ok.calls) == 1  # same (lib, version, hint) only once per learner

    L3 = _learner(world, tmp_path)
    rep = L3.record(world.episode("g4", "2.0", _code(world, "nothing()"), False, **err))
    assert any("no ingestor" in n for n in rep.notes)


def test_provenance_episode_and_used_edges(world, tmp_path):
    L = _learner(world, tmp_path)
    ep = world.episode("p1", "2.0", _code(world, "sub.new_fn(x) + " + world.mod + ".concat([x])"), True,
                       problem=f"{world.words['bessel']} values", context=world.chain_ctx("2.0"))
    L.record(ep)
    key = f"{world.run_id}|p1|1"
    eid = node_id(EPISODE, key)
    props = node(world.graph, EPISODE, eid, ("run_id", "problem_id", "library", "version", "passed", "iteration",
                                             "error_type"))
    assert props == {"run_id": world.run_id, "problem_id": "p1", "library": world.lib, "version": "2.0", "passed": True,
                     "iteration": 1, "error_type": ""}
    used = {r["dst"]: r for r in edges_from(world.graph, [eid], USED, props=("helped", "via", "cue_terms"))}
    assert set(used) == {world.api("sub.new_fn"), world.api("concat")}
    assert all(r["helped"] is True for r in used.values())
    nf = used[world.api("sub.new_fn")]
    assert nf["via"] == f"{world.p('old_fn')}>{world.p('mid_fn')}>{world.p('sub.new_fn')}"
    assert nf["cue_terms"] == world.words["bessel"]  # the problem word that cues the chain's first API
    # recording the same episode again changes nothing
    rep = L.record(ep)
    assert rep.edges_reweighted == 0 and "already recorded" in rep.notes[0]
    assert world.edge(REPLACED_BY, world.api("old_fn"), world.api("mid_fn"))["w"] == 1.0 + ALPHA


def test_outcome_without_context_still_records(world, tmp_path):
    """Product hook: report_outcome from an agent that never asked Pinned (context=None, maybe no problem_id)."""
    L = GraphLearner(world.graph, run_id=world.run_id, log_dir=tmp_path)
    ep = world.episode("", "2.0", _code(world, "concat([a, b])"), True, problem=f"stack frames {world.words['concat']}")
    rep = L.record(ep)
    assert world.edge(CUES, world.term("concat"), world.api("concat"))["w"] == 1.0 + ALPHA
    eps = [m for m in L.log.read() if m["move"] == "episode"]
    assert len(eps) == 1 and eps[0]["keys"]["node"].startswith(f"{world.run_id}|live-")
    assert eps[0]["after"]["used"] == [world.p("concat")]
    assert any("no context" in n for n in rep.notes)
    # code that does not parse still leaves an episode (and gaps from the error text)
    rep = L.record(world.episode("", "2.0", "def (:", False, error_type="SyntaxError", error_text="invalid syntax"))
    assert any("did not parse" in n for n in rep.notes)


def test_move_log_lines(world, tmp_path):
    L = _learner(world, tmp_path)
    L.record(world.episode("p1", "2.0", _code(world), True, problem=world.words["bessel"], context=world.chain_ctx("2.0")))
    path = tmp_path / f"{world.run_id}.jsonl"
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    assert {m["move"] for m in lines} == {"reweight", "learn_cue", "episode"}
    rw = next(m for m in lines if m["move"] == "reweight")
    assert set(rw) == {"ts", "run_id", "move", "keys", "before", "after", "why", "episodes"}
    assert rw["keys"] == {"src": f"{world.lib}|{world.p('old_fn')}", "rel": REPLACED_BY,
                          "dst": f"{world.lib}|{world.p('mid_fn')}"}
    assert rw["before"]["w"] == 1.0 and rw["after"]["w"] == 1.0 + ALPHA
    assert rw["episodes"] == [f"{world.run_id}|p1|1"] and rw["why"].startswith("pass at")


def test_method_on_unresolved_receiver_matches_item(world, tmp_path):
    L = _learner(world, tmp_path)
    ctx = Context(world.lib, "1.0", [ContextItem(world.lib, world.p("Frame.append"), "valid")])
    code = f"import {world.mod}\ndef f(df, row):\n    return df.append(row)\n"
    L.record(world.episode("p1", "1.0", code, True, problem=f"add a row to a {world.words['frame']}", context=ctx))
    assert world.edge(CUES, world.term("frame"), world.api("Frame.append"))["w"] == 1.0 + ALPHA


def test_bad_version_is_a_clear_error(world, tmp_path):
    L = _learner(world, tmp_path)
    with pytest.raises(ValueError, match="not a valid version"):
        L.record(world.episode("p1", "not-a-version", _code(world), True))


def test_used_items_below_floor_still_counted(world, tmp_path):
    """Weights are bounded at W_MIN: a long failure streak never goes negative."""
    L = _learner(world, tmp_path)
    for i in range(6):
        L.record(world.episode(f"f{i}", "2.0", _code(world, "concat([1])"), False, problem=world.words["concat"],
                               error_type="AssertionError", error_text="no"))
    e = world.edge(CUES, world.term("concat"), world.api("concat"))
    assert e["w"] == 0.0 < W_FLOOR and e["n_fail"] == 6
