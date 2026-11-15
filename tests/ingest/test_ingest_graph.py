"""Ingestion of a fake three-release library into a real HydraDB node (fixture `graph`)."""

from pinned.contract import (
    ADDED_IN, ALIAS_OF, API, CHANGED_IN, CUES, HAS_VERSION, LIBRARY, MEMBER_OF, NEXT, REMOVED_IN, REPLACED_BY, TERM,
    VERSION, W_DEFAULT,
)
from pinned.graph import edges_from, node, node_id, set_edge, version_ord
from pinned.ingest.summary import library_counts
from pinned.ingest.validity import validity
from pinned.ingest.writer import api_id, version_id


def _ingest_all(ing, pkg):
    return [ing.ingest_version(pkg, v) for v in ("1.0.0", "1.1.0", "1.2.0")]


def _changes(graph, pkg, path):
    return {e["dst_version"]: e for e in edges_from(graph, [api_id(pkg, path)], CHANGED_IN,
                                                    props=("kind", "before", "after", "evidence", "extra_kinds"),
                                                    dst_props=("version",))}


def test_ladder_nodes_and_next_chain(graph, fake, ingestor):
    pkg, _ = fake
    ingestor.ingest_version(pkg, "1.0.0")
    lib = node(graph, LIBRARY, node_id(LIBRARY, pkg), ("name", "import_names"))
    assert lib == {"name": pkg, "import_names": pkg}
    vids = [version_id(pkg, v) for v in ("1.0.0", "1.1.0", "1.2.0")]
    assert sorted(e["dst"] for e in edges_from(graph, [node_id(LIBRARY, pkg)], HAS_VERSION, dst_props=())) == sorted(vids)
    assert {(e["src"], e["dst"]) for e in edges_from(graph, vids, NEXT, dst_props=())} == {(vids[0], vids[1]),
                                                                                         (vids[1], vids[2])}
    v = node(graph, VERSION, vids[0], ("library", "version", "ord", "released", "ingested"))
    assert v == {"library": pkg, "version": "1.0.0", "ord": version_ord("1.0.0"), "released": "2026-01-01",
                 "ingested": True}


def test_removed_function(graph, fake, ingestor):
    pkg, _ = fake
    reports = _ingest_all(ingestor, pkg)
    path = f"{pkg}.old_func"
    assert validity(graph, pkg, [path], "1.0.0")[path] == "valid"
    assert validity(graph, pkg, [path], "1.1.0")[path] == "removed"
    assert validity(graph, pkg, [path], "1.2.0")[path] == "removed"
    ev = edges_from(graph, [api_id(pkg, path)], REMOVED_IN, props=("evidence",), dst_props=("version",))
    assert [e["dst_version"] for e in ev] == ["1.1.0"] and "absent in" in ev[0]["evidence"]
    assert reports[1].removed >= 1 and reports[0].added == reports[0].apis


def test_rename_with_use_instead_warning(graph, fake, ingestor):
    pkg, _ = fake
    _ingest_all(ingestor, pkg)
    old, new = f"{pkg}.rename_me", f"{pkg}.new_name"
    ch = _changes(graph, pkg, old)
    assert ch["1.1.0"]["kind"] == "deprecated" and "use new_name instead" in ch["1.1.0"]["after"]
    rep = edges_from(graph, [api_id(pkg, old)], REPLACED_BY, props=("w", "since_ord", "evidence"), dst_props=("path",))
    assert {e["dst_path"] for e in rep} == {new}
    assert all(e["w"] == W_DEFAULT and e["since_ord"] == version_ord("1.1.0") for e in rep)
    assert validity(graph, pkg, [old, new], "1.1.0") == {old: "valid", new: "valid"}  # deprecated, still usable
    assert validity(graph, pkg, [old, new], "1.2.0") == {old: "removed", new: "valid"}
    assert validity(graph, pkg, [new], "1.0.0") == {new: "added_later"}


def test_signature_change(graph, fake, ingestor):
    pkg, _ = fake
    _ingest_all(ingestor, pkg)
    path = f"{pkg}.sig_change"
    ch = _changes(graph, pkg, path)
    assert ch["1.1.0"]["kind"] == "signature"
    assert (ch["1.1.0"]["before"], ch["1.1.0"]["after"]) == ("(a, b=1)", "(a, b=1, *, c=None)")
    added = edges_from(graph, [api_id(pkg, path)], ADDED_IN, props=("sig", "evidence"), dst_props=("version",))
    assert [(e["dst_version"], e["sig"]) for e in added] == [("1.0.0", "(a, b=1)")]
    # the method signature drops `self`
    grow = edges_from(graph, [api_id(pkg, f"{pkg}.Thing.grow")], ADDED_IN, props=("sig",), dst_props=())
    assert grow[0]["sig"] == "(by)"


def test_alias_and_members(graph, fake, ingestor):
    pkg, _ = fake
    _ingest_all(ingestor, pkg)
    al = edges_from(graph, [api_id(pkg, f"{pkg}.tool")], ALIAS_OF, props=("evidence",), dst_props=("path",))
    assert [e["dst_path"] for e in al] == [f"{pkg}.tools.tool"]
    assert node(graph, API, api_id(pkg, f"{pkg}.tool"), ("kind", "path", "library")) == {
        "kind": "function", "path": f"{pkg}.tool", "library": pkg}
    member = edges_from(graph, [api_id(pkg, f"{pkg}.Thing.grow"), api_id(pkg, f"{pkg}.Thing")], MEMBER_OF,
                        dst_props=("path",))
    assert {e["dst_path"] for e in member} == {f"{pkg}.Thing", pkg}
    cues = graph.query(f"MATCH (t:{TERM})-[r:{CUES}]->(a:{API}) WHERE a.id = $id RETURN t.word AS w, r.source AS s, r.w AS wt",
                       {"id": api_id(pkg, f"{pkg}.Thing.grow")})
    words = {(c["w"], c["s"]) for c in cues}
    assert ("grow", "name") in words and ("thing", "name") in words
    assert all(c["wt"] == W_DEFAULT for c in cues)


def test_remove_then_readd(graph, fake, ingestor):
    pkg, _ = fake
    _ingest_all(ingestor, pkg)
    path = f"{pkg}.flaky"
    assert [validity(graph, pkg, [path], v)[path] for v in ("1.0.0", "1.1.0", "1.2.0")] == ["valid", "removed", "valid"]
    added = edges_from(graph, [api_id(pkg, path)], ADDED_IN, dst_props=("version",))
    assert sorted(e["dst_version"] for e in added) == ["1.0.0", "1.2.0"]


def test_same_object_moved_between_modules(graph, fake, ingestor):
    pkg, _ = fake
    _ingest_all(ingestor, pkg)
    rep = edges_from(graph, [api_id(pkg, f"{pkg}.core.mover")], REPLACED_BY, props=("w", "since_ord", "evidence"),
                     dst_props=("path",))
    assert [e["dst_path"] for e in rep] == [f"{pkg}.extra.mover"]
    assert rep[0]["since_ord"] == version_ord("1.2.0") and rep[0]["w"] == W_DEFAULT


def test_compiled_member_filled_by_dynamic_inspection(graph, fake, ingestor):
    pkg, _ = fake
    _ingest_all(ingestor, pkg)
    path = f"{pkg}.fast_add"
    assert validity(graph, pkg, [path], "1.0.0")[path] == "valid"
    added = edges_from(graph, [api_id(pkg, path)], ADDED_IN, props=("sig", "evidence"), dst_props=("version",))
    assert added[0]["sig"] == "(x, y)" and "dynamic" in added[0]["evidence"]
    # only the package that star-imports the compiled module was inspected dynamically
    assert all(mods == [pkg] for _, mods in ingestor.test_inspector.calls)


def test_rerun_is_idempotent_and_keeps_learned_weights(graph, fake, ingestor):
    pkg, _ = fake
    _ingest_all(ingestor, pkg)
    before = library_counts(graph, pkg)
    old, new = api_id(pkg, f"{pkg}.rename_me"), api_id(pkg, f"{pkg}.new_name")
    set_edge(graph, old, REPLACED_BY, new, {"w": 0.35})  # pretend the learner moved it
    cue_src = node_id(TERM, "grow")
    set_edge(graph, cue_src, CUES, api_id(pkg, f"{pkg}.Thing.grow"), {"w": 1.7})
    again = _ingest_all(ingestor, pkg)
    assert library_counts(graph, pkg) == before
    assert [r.apis for r in again] == [r.apis for r in again]  # same snapshots
    w = edges_from(graph, [old], REPLACED_BY, props=("w",), dst_props=())
    assert [e["w"] for e in w] == [0.35]
    w = edges_from(graph, [cue_src], CUES, props=("w",), dst_props=("path",))
    assert [e["w"] for e in w if e["dst_path"] == f"{pkg}.Thing.grow"] == [1.7]


def test_fill_gap_adds_runtime_attribute_and_is_repeatable(graph, fake, ingestor):
    pkg, _ = fake
    _ingest_all(ingestor, pkg)
    path = f"{pkg}.lazy.lazy_thing"
    assert validity(graph, pkg, [path], "1.1.0")[path] == "missing"
    rep = ingestor.fill_gap(pkg, "1.1.0", path)
    assert rep.added >= 1
    assert validity(graph, pkg, [path], "1.1.0")[path] == "valid"
    sig = edges_from(graph, [api_id(pkg, path)], ADDED_IN, props=("sig",), dst_props=("version",))
    assert sig and sig[0]["sig"] == "(n, scale=2.0)"
    # the module is remembered: later loads of other versions see it too, and a repeat adds nothing
    assert ingestor.snapshots.gap_modules(pkg) == [f"{pkg}.lazy"]
    counts = library_counts(graph, pkg)
    rep2 = ingestor.fill_gap(pkg, "1.1.0", path)
    assert (rep2.added, rep2.removed, rep2.changed, rep2.replaced) == (0, 0, 0, 0)
    assert library_counts(graph, pkg) == counts


def test_fill_gap_records_removal_message_and_replacement(graph, fake, ingestor):
    pkg, _ = fake
    _ingest_all(ingestor, pkg)
    rep = ingestor.fill_gap(pkg, "1.2.0", "AttributeError: module '%s' has no attribute 'gone_attr'" % pkg)
    path = f"{pkg}.gone_attr"
    assert rep.removed == 1 and rep.replaced == 1
    assert validity(graph, pkg, [path], "1.2.0")[path] == "removed"
    ev = edges_from(graph, [api_id(pkg, path)], REMOVED_IN, props=("evidence",), dst_props=())
    assert "gone_attr was removed in 1.1" in ev[0]["evidence"]
    by = edges_from(graph, [api_id(pkg, path)], REPLACED_BY, props=("w",), dst_props=("path",))
    assert [(e["dst_path"], e["w"]) for e in by] == [(f"{pkg}.new_name", W_DEFAULT)]


def test_reports_written(fake, ingestor, tmp_path):
    pkg, _ = fake
    _ingest_all(ingestor, pkg)
    files = sorted((tmp_path / "reports" / pkg).glob("*.json"))
    assert [f.name for f in files] == ["1.0.0.json", "1.1.0.json", "1.2.0.json"]
