"""HTTP API: every endpoint (happy path, validation errors, HydraDB down), error shape, timing headers."""

from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient
from server_fakes import GraphBackedSearch, RecordingLearner

from pinned.contract import EPISODE
from pinned.graph import node, node_id
from pinned.server import create_app


def test_check_finds_removed_call_with_fix_and_chain(client, lib):
    code = f"import {lib}\nx = {lib}.old_sum([1, 2])\ny = {lib}.mean([1, 2])\n"
    r = client.post("/v1/check", json={"code": code, "library": lib, "version": "2.0"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["version"] == "2.0.0"  # 2.0 resolved to the graph's 2.0.0
    assert d["ok"] is False
    by_call = {f["call"]: f for f in d["findings"]}
    old = by_call[f"{lib}.old_sum"]
    assert old["status"] == "removed" and old["fix"] == f"{lib}.total" and old["line"] == 2
    assert [h["rel"] for h in old["chain"]] == ["REMOVED_IN", "REPLACED_BY"]
    assert by_call[f"{lib}.mean"]["status"] == "valid"
    assert d["took_ms"] >= 0
    assert "x-response-time-ms" in r.headers and r.headers["server-timing"].startswith("app;dur=")


def test_check_all_valid_at_old_version(client, lib):
    r = client.post("/v1/check", json={"code": f"{lib}.old_sum(x)", "library": lib, "version": "1.0.0"})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_check_validation_errors(client, lib, settings):
    r = client.post("/v1/check", json={"code": "x", "library": lib})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "bad_input" and err["details"]["problems"][0]["field"] == "version"
    r = client.post("/v1/check", json={"code": "x", "library": lib, "version": "2.0", "extra": 1})
    assert r.status_code == 422
    r = client.post("/v1/check", json={"code": "   ", "library": lib, "version": "2.0"})
    assert r.status_code == 422 and r.json()["error"]["message"] == "code is empty"
    big = "x" * (settings.max_code_bytes + 1)
    r = client.post("/v1/check", json={"code": big, "library": lib, "version": "2.0"})
    assert r.status_code == 422 and r.json()["error"]["details"]["limit_bytes"] == settings.max_code_bytes
    r = client.post("/v1/check", json={"code": "x", "library": lib, "version": "not a version"})
    assert r.status_code == 422 and "not a valid version" in r.json()["error"]["message"]
    r = client.post("/v1/check", json={"code": "x", "library": "Bad Name!", "version": "1.0"})
    assert r.status_code == 422
    r = client.post("/v1/check", content=b"{not json", headers={"content-type": "application/json"})
    assert r.status_code == 422


def test_unknown_library_and_version_are_404_with_hints(client, lib):
    r = client.post("/v1/check", json={"code": "x", "library": "nosuchlib-zz", "version": "1.0"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found" and lib in r.json()["error"]["details"]["known_libraries"]
    r = client.post("/v1/check", json={"code": "x", "library": lib, "version": "1.7"})
    d = r.json()["error"]["details"]
    assert r.status_code == 404
    assert d["known_versions"] == ["1.0.0", "1.5.0", "2.0.0"]
    assert (d["nearest_lower"], d["nearest_higher"]) == ("1.5.0", "2.0.0")


def test_library_name_is_case_insensitive(client, lib):
    r = client.post("/v1/check", json={"code": f"{lib}.mean(x)", "library": lib.upper(), "version": "1.5.0"})
    assert r.status_code == 200 and r.json()["library"] == lib


def test_how_returns_items_chains_and_prompt(client, lib):
    r = client.post("/v1/how", json={"task": "sum the values", "library": lib, "version": "2.0.0", "k": 5})
    assert r.status_code == 200, r.text
    d = r.json()
    items = {i["path"]: i for i in d["items"]}
    assert items[f"{lib}.old_sum"]["status"] == "removed"
    assert items[f"{lib}.total"]["status"] == "valid"
    assert items[f"{lib}.total"]["signature"] == "(x, *, skipna=True)"
    assert d["prompt"].startswith(f"Facts for {lib}==2.0.0")
    assert d["graph_queries"] > 0


def test_how_signature_differs_per_version(client, lib):
    d = client.post("/v1/how", json={"task": "total", "library": lib, "version": "1.5.0"}).json()
    assert d["items"][0]["path"] == f"{lib}.total" and d["items"][0]["signature"] == "(x)"


def test_how_validation(client, lib, settings):
    base = {"task": "sum", "library": lib, "version": "2.0.0"}
    assert client.post("/v1/how", json={**base, "k": 0}).status_code == 422
    r = client.post("/v1/how", json={**base, "k": settings.max_k + 1})
    assert r.status_code == 422 and "k must be" in r.json()["error"]["message"]
    assert client.post("/v1/how", json={**base, "task": ""}).status_code == 422
    assert client.post("/v1/how", json={**base, "task": "x" * 4001}).status_code == 422


def test_explain_timeline(client, lib):
    r = client.get(f"/v1/api/{lib}/{lib}.old_sum")
    assert r.status_code == 200, r.text
    d = r.json()
    assert [(v["version"], v["status"]) for v in d["versions"]] == [
        ("1.0.0", "valid"), ("1.5.0", "valid"), ("2.0.0", "removed")]
    assert d["replaced_by"][0]["dst"] == f"{lib}.total" and d["kind"] == "function"


def test_explain_errors(client, lib):
    r = client.get(f"/v1/api/{lib}/{lib}.never_existed")
    assert r.status_code == 404 and "no facts" in r.json()["error"]["message"]
    assert client.get(f"/v1/api/{lib}/not a path").status_code == 422
    assert client.get(f"/v1/api/nosuchlib-zz/{lib}.mean").status_code == 404


def test_outcome_records_episode_and_learns(client, lib, learner, graph):
    task = "sum the values quickly"
    how = client.post("/v1/how", json={"task": task, "library": lib, "version": "2.0.0"}).json()
    body = {"code": f"{lib}.total(x)", "library": lib, "version": "2.0.0", "passed": True, "task": task}
    r = client.post("/v1/outcome", json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["recorded"] and d["used_context"] and d["episode_id"].startswith("live|live-")
    assert d["learned"]["edges_reweighted"] >= 1
    ep = learner.episodes[-1]
    assert ep.run_id == "live" and ep.passed and ep.problem == task
    # the remembered context from /v1/how was linked, not a fresh one
    assert [i.path for i in ep.context.items] == [i["path"] for i in how["items"]]
    stored = node(graph, EPISODE, node_id(EPISODE, d["episode_id"]), ("run_id", "passed", "library"))
    assert stored == {"run_id": "live", "passed": True, "library": lib}


def test_outcome_without_task_and_failure(client, lib, learner):
    body = {"code": f"{lib}.old_sum(x)", "library": lib, "version": "2.0.0", "passed": False,
            "error_type": "AttributeError", "error_text": f"module '{lib}' has no attribute 'old_sum'"}
    d = client.post("/v1/outcome", json=body).json()
    assert d["used_context"] is False and learner.episodes[-1].error_type == "AttributeError"


def test_outcome_validation(client, lib):
    base = {"code": "x", "library": lib, "version": "2.0.0"}
    assert client.post("/v1/outcome", json=base).status_code == 422  # passed missing
    assert client.post("/v1/outcome", json={**base, "passed": "maybe"}).status_code == 422
    assert client.post("/v1/outcome", json={**base, "passed": True, "error_type": "E" * 101}).status_code == 422


def test_outcome_triggers_background_maintain(graph, lib, settings):
    settings.maintain_every = 2
    learner = RecordingLearner(graph)
    with TestClient(create_app(GraphBackedSearch(graph), learner, graph, settings)) as c:
        body = {"code": f"{lib}.mean(x)", "library": lib, "version": "2.0.0", "passed": True}
        assert c.post("/v1/outcome", json=body).json()["maintain_scheduled"] is False
        assert c.post("/v1/outcome", json=body).json()["maintain_scheduled"] is True
    for _ in range(100):
        if learner.maintained:
            break
        time.sleep(0.05)
    assert learner.maintained == 1


def test_libraries(client, lib):
    r = client.get("/v1/libraries")
    assert r.status_code == 200
    mine = [li for li in r.json()["libraries"] if li["name"] == lib][0]
    assert [v["version"] for v in mine["versions"]] == ["1.0.0", "1.5.0", "2.0.0"]
    assert mine["import_names"] == [lib] and mine["versions"][0]["released"] == "2020-01-01"


def test_stats_counts_and_latency_from_evidence(client, settings):
    (settings.evidence_dir / "search").mkdir(parents=True)
    (settings.evidence_dir / "search" / "latency.json").write_text(json.dumps({
        "utc": "2026-11-15T00:00:00+00:00",
        "context_for": [{"concurrency": 1, "p50_ms": 5.0, "p95_ms": 9.0, "qps": 100.0}],
    }))
    r = client.get("/v1/stats")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["graph"]["nodes"]["Api"] >= 3 and d["graph"]["edges"]["REPLACED_BY"] >= 1
    assert d["graph"]["total_nodes"] == sum(d["graph"]["nodes"].values())
    s = d["latency"]["search"]
    assert s["file"].endswith("search/latency.json")
    assert s["latency"]["context_for"] == [{"concurrency": 1, "p50_ms": 5.0, "p95_ms": 9.0, "qps": 100.0}]
    assert d["latency"]["graph_layer"] is None  # tmp evidence dir has no load bench


def test_stats_reads_real_load_bench():
    from pinned.server.config import ROOT
    from pinned.server.evidence import latency_summary

    s = latency_summary(ROOT / "evidence", "load")
    assert s is not None and s["file"].startswith("evidence/load/")
    assert {r["concurrency"] for r in s["latency"]["reads.cues_top20"]} == {1, 8, 32}


def test_curve_404_then_served(client, settings):
    r = client.get("/v1/curve")
    assert r.status_code == 404 and "curve.json" in r.json()["error"]["message"]
    settings.evidence_dir.mkdir(parents=True, exist_ok=True)
    (settings.evidence_dir / "curve.json").write_text(json.dumps({"points": [{"iteration": 0, "pass_at_1": 0.1}]}))
    r = client.get("/v1/curve")
    assert r.status_code == 200 and r.json()["curve"]["points"][0]["iteration"] == 0
    (settings.evidence_dir / "curve.json").write_text("{broken")
    r = client.get("/v1/curve")
    assert r.status_code == 500 and r.json()["error"]["code"] == "evidence_unreadable"


def test_healthz_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["hydradb"]["ok"] is True


def test_unknown_route_is_json_404(client):
    r = client.get("/v1/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_openapi_lists_every_endpoint(client):
    paths = client.get("/openapi.json").json()["paths"]
    for p in ("/v1/check", "/v1/how", "/v1/api/{library}/{path}", "/v1/outcome", "/v1/libraries", "/v1/stats",
              "/v1/curve", "/healthz"):
        assert p in paths


# ---------------------------------------------------------------------------------------------------- HydraDB down
def test_every_graph_endpoint_returns_503_when_hydradb_is_down(down_client):
    calls = [
        ("post", "/v1/check", {"code": "x.f()", "library": "x", "version": "1.0"}),
        ("post", "/v1/how", {"task": "t", "library": "x", "version": "1.0"}),
        ("get", "/v1/api/x/x.f", None),
        ("post", "/v1/outcome", {"code": "x", "library": "x", "version": "1.0", "passed": True}),
        ("get", "/v1/libraries", None),
        ("get", "/v1/stats", None),
    ]
    for method, path, body in calls:
        r = getattr(down_client, method)(path, **({"json": body} if body else {}))
        assert r.status_code == 503, (path, r.text)
        assert r.json()["error"]["code"] == "graph_unavailable", path
        assert "not reachable" in r.json()["error"]["message"]


def test_healthz_503_when_hydradb_is_down(down_client):
    r = down_client.get("/healthz")
    assert r.status_code == 503 and r.json()["status"] == "degraded" and r.json()["hydradb"]["ok"] is False


def test_validation_still_422_when_hydradb_is_down(down_client):
    r = down_client.post("/v1/how", json={"task": "t", "library": "x", "version": "1.0", "k": 999})
    assert r.status_code == 422  # input errors are reported before any graph call


def test_curve_works_without_hydradb(down_client):
    assert down_client.get("/v1/curve").status_code == 404
