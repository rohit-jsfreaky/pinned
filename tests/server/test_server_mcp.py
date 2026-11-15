"""MCP tools through the SDK's in-memory Client, over stdio (`pinned mcp`), and /mcp over real HTTP in-process."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from server_fakes import GraphBackedSearch, RecordingLearner, free_port

from pinned.server import PinnedService, Settings, build_mcp, create_app

pytestmark = pytest.mark.anyio
HERE = Path(__file__).resolve().parent


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.fixture()
def mcp_server(graph, lib, learner, settings):
    return build_mcp(PinnedService(GraphBackedSearch(graph), learner, graph, settings))


async def test_lists_four_tools_with_agent_descriptions_and_schemas(mcp_server):
    async with Client(mcp_server) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        assert set(tools) == {"check_code", "how_to", "explain_api", "report_outcome"}
        for t in tools.values():
            assert len(t.description) > 150 and "Call this" in t.description
            assert t.output_schema and t.output_schema.get("type") == "object"
        assert set(tools["how_to"].input_schema["required"]) == {"task", "library", "version"}
        assert tools["check_code"].annotations.read_only_hint is True
        assert tools["report_outcome"].annotations.read_only_hint is False
        assert "report_outcome" in (c.instructions or "")


async def test_check_code_text_and_structured(mcp_server, lib):
    async with Client(mcp_server) as c:
        r = await c.call_tool("check_code", {"code": f"{lib}.old_sum(x)\n{lib}.mean(x)", "library": lib,
                                             "version": "2.0.0"})
        assert not r.is_error
        text = r.content[0].text
        assert f"1 of 2 calls need attention" in text
        assert f"line 1: {lib}.old_sum: removed -> use {lib}.total" in text
        assert f"[{lib}.old_sum -REMOVED_IN-> {lib} 2.0.0 ; {lib}.old_sum -REPLACED_BY-> {lib}.total]" in text
        s = r.structured_content
        assert s["ok"] is False and s["library"] == lib and len(s["findings"]) == 2


async def test_how_to_returns_one_line_per_item(mcp_server, lib):
    async with Client(mcp_server) as c:
        r = await c.call_tool("how_to", {"task": "sum numbers", "library": lib, "version": "2.0", "k": 4})
        assert not r.is_error
        lines = r.content[0].text.splitlines()
        assert lines[0] == f"Facts for {lib}==2.0.0 (from the Pinned graph):"
        assert any(f"{lib}.total: valid. signature: (x, *, skipna=True)" in ln for ln in lines)
        assert any(f"{lib}.old_sum: removed." in ln and "REPLACED_BY" in ln for ln in lines)
        assert {i["path"] for i in r.structured_content["items"]} == {f"{lib}.old_sum", f"{lib}.total"}


async def test_explain_api_timeline_text(mcp_server, lib):
    async with Client(mcp_server) as c:
        r = await c.call_tool("explain_api", {"library": lib, "path": f"{lib}.total"})
        text = r.content[0].text
        assert f"- 1.0.0: added_later" in text
        assert f"- 1.5.0: valid (x)" in text and "- 2.0.0: valid (x, *, skipna=True)" in text
        assert [v["status"] for v in r.structured_content["versions"]] == ["added_later", "valid", "valid"]


async def test_report_outcome_feeds_the_learner(mcp_server, lib, learner):
    async with Client(mcp_server) as c:
        await c.call_tool("how_to", {"task": "average", "library": lib, "version": "2.0.0"})
        r = await c.call_tool("report_outcome", {"code": f"{lib}.mean(x)", "library": lib, "version": "2.0.0",
                                                 "passed": True, "task": "average"})
        assert not r.is_error and r.content[0].text.startswith(f"Recorded pass for {lib}==2.0.0")
        assert r.structured_content["used_context"] is True
        assert learner.episodes[-1].context.items[0].path == f"{lib}.mean"


async def test_tool_errors_are_readable(mcp_server, lib):
    async with Client(mcp_server) as c:
        r = await c.call_tool("check_code", {"code": "x", "library": lib, "version": "9.9"})
        assert r.is_error
        assert "not_found" in r.content[0].text and "known_versions" in r.content[0].text
        r = await c.call_tool("how_to", {"task": "x", "library": lib, "version": "2.0", "k": 99})
        assert r.is_error  # schema rejects k > 32
        r = await c.call_tool("explain_api", {"library": lib, "path": "bad path"})
        assert r.is_error and "dotted import path" in r.content[0].text


async def test_tools_report_hydradb_down(dead_graph, settings):
    server = build_mcp(PinnedService(GraphBackedSearch(dead_graph), RecordingLearner(dead_graph), dead_graph, settings))
    async with Client(server) as c:
        r = await c.call_tool("check_code", {"code": "x.f()", "library": "x", "version": "1.0"})
        assert r.is_error and "graph_unavailable" in r.content[0].text


async def test_stdio_transport_via_cli(graph, lib):
    env = {**os.environ, "HYDRA_URL": graph.url, "PINNED_FACTORY": "server_fakes:build_services",
           "PYTHONPATH": str(HERE)}
    params = StdioServerParameters(command=sys.executable, args=["-m", "pinned.cli", "mcp"], env=env)
    async with Client(params) as c:
        assert len((await c.list_tools()).tools) == 4
        r = await c.call_tool("check_code", {"code": f"{lib}.old_sum(1)", "library": lib, "version": "2.0.0"})
        assert not r.is_error and r.structured_content["findings"][0]["fix"] == f"{lib}.total"


class _Server:
    """uvicorn in a background thread of this process, on a free localhost port."""

    def __init__(self, app):
        self.port = free_port()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        for _ in range(200):
            if self.server.started:
                return self
            time.sleep(0.02)
        raise RuntimeError("uvicorn did not start")

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(10)


async def test_mcp_endpoint_over_http_in_process(graph, lib, learner, settings):
    app = create_app(GraphBackedSearch(graph), learner, graph, settings)
    with _Server(app) as s:
        async with Client(f"http://127.0.0.1:{s.port}/mcp") as c:
            assert {t.name for t in (await c.list_tools()).tools} == {"check_code", "how_to", "explain_api",
                                                                      "report_outcome"}
            r = await c.call_tool("explain_api", {"library": lib, "path": f"{lib}.old_sum"})
            assert not r.is_error and r.structured_content["replaced_by"][0]["dst"] == f"{lib}.total"
        # the REST API answers on the same server
        assert httpx.get(f"http://127.0.0.1:{s.port}/healthz").status_code == 200


async def test_mcp_endpoint_rejects_foreign_host_unless_allowed(graph, lib, learner, settings):
    with _Server(create_app(GraphBackedSearch(graph), learner, graph, settings)) as s:
        r = httpx.post(f"http://127.0.0.1:{s.port}/mcp", headers={"Host": "evil.example", "Accept":
                       "application/json, text/event-stream"}, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert r.status_code == 421
    allowed = Settings(**{**settings.__dict__, "allowed_hosts": ["pinned.example.com"]})
    with _Server(create_app(GraphBackedSearch(graph), learner, graph, allowed)) as s:
        async with httpx.AsyncClient(headers={"Host": "pinned.example.com"}) as http:
            r = await http.post(f"http://127.0.0.1:{s.port}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                headers={"Accept": "application/json, text/event-stream"})
            assert r.status_code == 200, r.text
            assert r.json()["id"] == 1 and "result" in r.json()
