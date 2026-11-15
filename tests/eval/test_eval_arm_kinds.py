"""`curve --arm-kind graph|plain|shuffled`: each kind builds the right (search, learner) pair."""

import argparse

import pytest
from eval_fakes import make_problem

import pinned.eval.__main__ as cli
from pinned.learn.plain import PlainStore
from pinned.learn.shuffled import ShuffledSearch

PROBLEMS = [make_problem("0", "pandas"), make_problem("1", "numpy"), make_problem("2", "pandas")]


class FakeSearch:
    def __init__(self, g):
        self.g = g


class FakeLearner:
    def __init__(self, g, ingestor=None, run_id="", split_path=""):
        self.g, self.ingestor, self.run_id, self.split_path = g, ingestor, run_id, split_path


def _args(**kw):
    return argparse.Namespace(run_id="r1", split_path="evidence/split.json", no_ingest=True, **kw)


def test_graph_kind_is_todays_behaviour():
    g = object()
    s, l, info = cli.curve_parts("graph", g, _args(), PROBLEMS, FakeSearch, FakeLearner)
    assert isinstance(s, FakeSearch) and s.g is g
    assert isinstance(l, FakeLearner) and l.g is g and l.run_id == "r1"
    assert info == {"kind": "graph"}


def test_plain_kind_is_one_plainstore_for_search_and_learner(monkeypatch):
    seen = {}

    def fake_from_graph(graph, library, split_path=None):
        seen["libs"], seen["split"] = list(library), split_path
        return PlainStore(split_path=None)

    monkeypatch.setattr(PlainStore, "from_graph", staticmethod(fake_from_graph))
    s, l, info = cli.curve_parts("plain", object(), _args(), PROBLEMS, FakeSearch, FakeLearner)
    assert isinstance(s, PlainStore) and s is l          # same dicts answer and learn
    assert seen["libs"] == ["numpy", "pandas"]           # every library in the dataset, once
    assert seen["split"] == "evidence/split.json"        # the split guard is on
    assert info["kind"] == "plain"


def test_shuffled_kind_wraps_graph_search_with_seed_13():
    g = object()
    s, l, info = cli.curve_parts("shuffled", g, _args(), PROBLEMS, FakeSearch, FakeLearner)
    assert isinstance(s, ShuffledSearch) and s.seed == 13 and isinstance(s.inner, FakeSearch)
    assert isinstance(l, FakeLearner) and l.g is g
    assert info["kind"] == "shuffled" and info["seed"] == 13


def test_unknown_kind_and_cli_flag():
    with pytest.raises(SystemExit):
        cli.curve_parts("bogus", object(), _args(), PROBLEMS, FakeSearch, FakeLearner)
    with pytest.raises(SystemExit):
        cli.main(["curve", "--run-id", "x", "--arm-kind", "bogus"])
