"""Every graph helper against a real HydraDB node (fixture in tests/conftest.py)."""

import pytest

from pinned.contract import ADDED_IN, ALIAS_OF, API, CUES, REMOVED_IN, REPLACED_BY, TERM, VERSION
from pinned.graph import (
    HydraError,
    count_edges,
    count_nodes,
    cypher_strings,
    delete_nodes,
    edges_from,
    link,
    link_many,
    link_props_many,
    multi_paths,
    node,
    node_id,
    paths,
    reach,
    set_edge,
    set_edges_many,
    unlink,
    upsert_node,
    upsert_nodes,
    version_ord,
)


def _apis(g, run, *names):
    return upsert_nodes(g, API, [{"key": f"{run}|{n}", "library": run, "path": n, "kind": "function"} for n in names])


def test_upsert_nodes_ids_props_and_idempotent(graph, run_key):
    ids = _apis(graph, run_key, "a.f", "a.g")
    assert ids == [node_id(API, f"{run_key}|a.f"), node_id(API, f"{run_key}|a.g")]
    assert node(graph, API, ids[0], ("key", "path", "kind")) == {"key": f"{run_key}|a.f", "path": "a.f", "kind": "function"}
    _apis(graph, run_key, "a.f", "a.g")  # re-run: same nodes, no duplicates
    rows = graph.query(f"MATCH (n:{API}) WHERE n.library = $lib RETURN n.id AS id", {"lib": run_key})
    assert sorted(r["id"] for r in rows) == sorted(ids)


def test_upsert_nodes_mixed_fields_and_none(graph, run_key):
    ids = upsert_nodes(graph, TERM, [{"key": f"{run_key}|x", "word": "x"}, {"key": f"{run_key}|y", "word": None}])
    assert node(graph, TERM, ids[0], ("word",)) == {"word": "x"}
    assert node(graph, TERM, ids[1], ("word",)) == {"word": None}


def test_upsert_nodes_rejects_bad_input(graph, run_key):
    with pytest.raises(ValueError):
        upsert_nodes(graph, API, [{"path": "no key"}])
    with pytest.raises(ValueError):
        upsert_nodes(graph, API, [{"key": f"{run_key}|l", "tags": ["a"]}])
    with pytest.raises(ValueError):
        upsert_nodes(graph, "Api) DETACH DELETE (n", [{"key": "x"}])


def test_link_many_is_idempotent_and_counts(graph, run_key):
    a, b = _apis(graph, run_key, "m.a", "m.b")
    v = upsert_node(graph, VERSION, f"{run_key}|1.0", library=run_key, version="1.0", ord=version_ord("1.0"))
    link_many(graph, ADDED_IN, [(a, v), (b, v), (a, v)])
    link_many(graph, ADDED_IN, [(a, v)])
    rows = edges_from(graph, [a, b], ADDED_IN)
    assert sorted((r["src"], r["dst"]) for r in rows) == sorted([(a, v), (b, v)])
    assert graph.query(f"MATCH (a:{API} {{id: $a}})-[:{ADDED_IN}]->(v) RETURN count(*) AS n", {"a": a})[0]["n"] == 1


def test_link_with_props_upserts_in_place(graph, run_key):
    a, b = _apis(graph, run_key, "p.a", "p.b")
    link(graph, a, REPLACED_BY, b, {"w": 1.0, "evidence": "doc"})
    link(graph, a, REPLACED_BY, b, {"w": 2.5, "evidence": "learned"})
    rows = edges_from(graph, [a], REPLACED_BY, props=("w", "evidence"))
    assert rows == [{"src": a, "dst": b, "w": 2.5, "evidence": "learned", "dst_key": f"{run_key}|p.b"}]
    set_edge(graph, a, REPLACED_BY, b, {"w": 0.5})
    assert edges_from(graph, [a], REPLACED_BY, props=("w",))[0]["w"] == 0.5
    unlink(graph, a, REPLACED_BY, b)
    assert edges_from(graph, [a], REPLACED_BY) == []


def test_link_props_many_batched(graph, run_key):
    t = upsert_node(graph, TERM, f"{run_key}|word", word="word")
    apis = _apis(graph, run_key, *[f"c.f{i}" for i in range(1500)])  # > one 1000-row chunk
    rows = [(t, x, {"w": i / 10, "source": "doc" if i % 2 else None}) for i, x in enumerate(apis)]
    link_props_many(graph, CUES, rows)
    link_props_many(graph, CUES, rows)  # idempotent
    got = edges_from(graph, [t], CUES, props=("w", "source"))
    assert len(got) == 1500 and {round(r["w"] * 10) for r in got} == set(range(1500))
    assert sum(r["source"] == "doc" for r in got) == 750
    set_edges_many(graph, CUES, [(t, apis[0], {"w": 42.0}), (t, 1, {"w": 1.0})])  # absent edge is skipped
    assert {r["dst"]: r["w"] for r in edges_from(graph, [t], CUES, props=("w",))}[apis[0]] == 42.0


def test_counts(graph, run_key):
    label = "T" + run_key  # private label so the count is exact
    ids = upsert_nodes(graph, label, [{"key": f"k{i}"} for i in range(5)])
    link_many(graph, ALIAS_OF, [(ids[0], ids[1]), (ids[1], ids[2])])
    assert count_nodes(graph, label) == 5
    assert count_edges(graph, label, ALIAS_OF) == 2


def test_paths_reach_and_multi_paths(graph, run_key):
    old, mid, new = _apis(graph, run_key, "x.old", "x.mid", "x.new")
    v2 = upsert_node(graph, VERSION, f"{run_key}|2.0", library=run_key, version="2.0", ord=version_ord("2.0"))
    link(graph, old, REPLACED_BY, mid, {"w": 1.0})
    link(graph, mid, ALIAS_OF, new, {"evidence": "griffe"})
    link_many(graph, REMOVED_IN, [(old, v2)])
    link_many(graph, ADDED_IN, [(new, v2)])

    ps = paths(graph, old, new, [REPLACED_BY, ALIAS_OF], max_len=2)
    assert len(ps) == 1 and ps[0].types == [REPLACED_BY, ALIAS_OF]
    assert [n["key"] for n in ps[0].nodes] == [f"{run_key}|x.old", f"{run_key}|x.mid", f"{run_key}|x.new"]
    assert ps[0].rels[0]["w"] == 1.0 and ps[0].rels[1]["evidence"] == "griffe"
    assert paths(graph, old, new, [REPLACED_BY, ALIAS_OF], max_len=1) == []  # max_len counts edges

    ends = {(tuple(p.types), p.end["id"]) for p in reach(graph, old, [REPLACED_BY, ALIAS_OF, REMOVED_IN], max_len=2)}
    assert ends == {((REPLACED_BY,), mid), ((REPLACED_BY, ALIAS_OF), new), ((REMOVED_IN,), v2)}

    back = reach(graph, v2, [ADDED_IN], max_len=1, direction="incoming")
    assert [p.end["id"] for p in back] == [new]

    mp = multi_paths(graph, API, [f"{run_key}|x.old", f"{run_key}|x.mid"], [f"{run_key}|x.new"],
                     [REPLACED_BY, ALIAS_OF], max_len=2)
    assert sorted((p.start["key"], len(p.rels)) for p in mp) == [(f"{run_key}|x.mid", 1), (f"{run_key}|x.old", 2)]


def test_multi_paths_quotes_in_keys(graph, run_key):
    a, b = upsert_nodes(graph, API, [{"key": f"{run_key}|o'brien\\x"}, {"key": f"{run_key}|b"}])
    link_many(graph, ALIAS_OF, [(a, b)])
    mp = multi_paths(graph, API, [f"{run_key}|o'brien\\x"], [f"{run_key}|b"], [ALIAS_OF], max_len=1)
    assert len(mp) == 1 and mp[0].start["key"] == f"{run_key}|o'brien\\x"
    assert cypher_strings(["a'b"]) == "['a\\'b']"


def test_delete_nodes(graph, run_key):
    a, b = _apis(graph, run_key, "d.a", "d.b")
    link_many(graph, ALIAS_OF, [(a, b)])
    delete_nodes(graph, [a, b])
    assert node(graph, API, a) is None and edges_from(graph, [a], ALIAS_OF) == []


def test_query_pages_through_everything(graph, run_key):
    label = "P" + run_key
    upsert_nodes(graph, label, [{"key": f"k{i}", "n": i} for i in range(2500)])
    rows = graph.query(f"MATCH (n:{label}) RETURN n.n AS n", page_size=700)
    assert sorted(r["n"] for r in rows) == list(range(2500))


def test_errors_are_raised_with_code(graph):
    with pytest.raises(HydraError) as e:
        graph.query("MATCH (a) RETURN a.id AS id")
    assert e.value.code == "invalid_request"
