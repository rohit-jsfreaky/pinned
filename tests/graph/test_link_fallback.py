"""link_many falls back to one MERGE per edge when HydraDB rejects a batch CREATE as `internal`
(0.4.0: idempotency-key clash after a node restarts from a saved store)."""

import pytest

from pinned.graph import ops
from pinned.graph.client import HydraError


class FakeGraph:
    def __init__(self, code):
        self.code, self.writes = code, []

    def write(self, q, params=None):
        if q.startswith("UNWIND"):
            raise HydraError(self.code, "boom", q, 500)
        self.writes.append((q, params))


def test_internal_error_falls_back_to_single_merges():
    g = FakeGraph("internal")
    assert ops.link_many(g, "CUES", [(1, 2), (1, 3), (1, 2)]) == 2
    assert [p for _, p in g.writes] == [{"src": 1, "dst": 2}, {"src": 1, "dst": 3}]
    assert all(q.startswith("MERGE (a {id: $src})-[:CUES]->") for q, _ in g.writes)


def test_other_errors_still_raise():
    with pytest.raises(HydraError):
        ops.link_many(FakeGraph("invalid_request"), "CUES", [(1, 2)])
