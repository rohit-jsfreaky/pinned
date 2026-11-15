"""Live run (scope item 6): seed a small graph on a real HydraDB node, start `pinned serve` as a separate process, call
every HTTP endpoint and every MCP tool (Streamable HTTP at /mcp and stdio via `pinned mcp`) for real, and write a
Markdown transcript.

    uv run python tests/server/live_run.py --hydra-url http://127.0.0.1:18643 --out <transcript.md>

Uses the test stand-ins for search/learn (server_fakes.py) until parts C and D merge; every call goes to HydraDB.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from server_fakes import free_port, seed_graph

from pinned.graph import HydraGraph

HERE = Path(__file__).resolve().parent
LIB = "pinneddemo"


def _fence(obj, limit: int = 2500) -> str:
    text = obj if isinstance(obj, str) else json.dumps(obj, indent=2)
    if len(text) > limit:
        text = text[:limit] + f"\n... ({len(text) - limit} more characters)"
    return f"```\n{text}\n```"


class Transcript:
    def __init__(self) -> None:
        self.parts: list[str] = []

    def add(self, title: str, request: str, status: str, body, timing: str = "") -> None:
        self.parts.append(f"#### {title}\n`{request}` → **{status}**{(' · ' + timing) if timing else ''}\n{_fence(body)}\n")


def http_calls(base: str, t: Transcript) -> None:
    code = f"import {LIB}\nvals = [1, 2, 3]\nprint({LIB}.old_sum(vals))\nprint({LIB}.mean(vals))\n"
    calls = [
        ("health", "GET", "/healthz", None),
        ("libraries", "GET", "/v1/libraries", None),
        ("check (2.0 has removed old_sum)", "POST", "/v1/check", {"code": code, "library": LIB, "version": "2.0"}),
        ("check (same code is fine on 1.0.0)", "POST", "/v1/check", {"code": code, "library": LIB, "version": "1.0.0"}),
        ("how", "POST", "/v1/how", {"task": "sum the values", "library": LIB, "version": "2.0.0", "k": 5}),
        ("explain", "GET", f"/v1/api/{LIB}/{LIB}.old_sum", None),
        ("outcome (pass, same task as how)", "POST", "/v1/outcome",
         {"code": f"print({LIB}.total(vals))", "library": LIB, "version": "2.0.0", "passed": True,
          "task": "sum the values"}),
        ("outcome (fail)", "POST", "/v1/outcome",
         {"code": code, "library": LIB, "version": "2.0.0", "passed": False, "error_type": "AttributeError",
          "error_text": f"AttributeError: module '{LIB}' has no attribute 'old_sum'", "task": "sum the values"}),
        ("stats", "GET", "/v1/stats", None),
        ("curve (no curve.json yet)", "GET", "/v1/curve", None),
        ("unknown version (error shape)", "POST", "/v1/how", {"task": "sum", "library": LIB, "version": "1.7"}),
        ("bad input (error shape)", "POST", "/v1/how", {"task": "sum", "library": LIB, "version": "2.0", "k": 0}),
    ]
    with httpx.Client(base_url=base, timeout=30) as c:
        for title, method, path, body in calls:
            r = c.request(method, path, json=body)
            req = f"{method} {path}" + (f" {json.dumps(body)[:160]}" if body else "")
            t.add(title, req, f"{r.status_code}", r.json(), f"X-Response-Time-Ms {r.headers.get('x-response-time-ms')}")


async def mcp_calls(base: str, env: dict, t: Transcript) -> None:
    code = f"import {LIB}\nprint({LIB}.old_sum([1, 2]))\n"
    tool_calls = [
        ("check_code", {"code": code, "library": LIB, "version": "2.0.0"}),
        ("how_to", {"task": "sum the values", "library": LIB, "version": "2.0.0", "k": 4}),
        ("explain_api", {"library": LIB, "path": f"{LIB}.total"}),
        ("report_outcome", {"code": f"print({LIB}.total([1, 2]))", "library": LIB, "version": "2.0.0", "passed": True,
                            "task": "sum the values"}),
    ]
    async with Client(f"{base}/mcp") as c:
        tools = (await c.list_tools()).tools
        t.add("MCP over HTTP: list_tools", f"POST {base}/mcp tools/list", "ok",
              [{"name": x.name, "description": x.description} for x in tools])
        for name, args in tool_calls:
            t0 = time.perf_counter()
            r = await c.call_tool(name, args)
            ms = round((time.perf_counter() - t0) * 1000, 1)
            t.add(f"MCP over HTTP: {name} (text the agent reads)", f"tools/call {name} {json.dumps(args)[:160]}",
                  "error" if r.is_error else "ok", r.content[0].text, f"{ms} ms round trip")
            t.add(f"MCP over HTTP: {name} (structured content)", f"tools/call {name}", "ok", r.structured_content)
    params = StdioServerParameters(command=sys.executable, args=["-m", "pinned.cli", "mcp"], env=env)
    async with Client(params) as c:
        names = [x.name for x in (await c.list_tools()).tools]
        r = await c.call_tool("check_code", tool_calls[0][1])
        t.add("MCP over stdio (`pinned mcp`): list_tools + check_code", "pinned mcp (stdio)",
              "error" if r.is_error else "ok", {"tools": names, "check_code text": r.content[0].text})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hydra-url", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    g = HydraGraph(url=args.hydra_url)
    if not g.ready():
        print(f"HydraDB is not answering at {args.hydra_url}", file=sys.stderr)
        return 1
    t_seed = time.perf_counter()
    seed_graph(g, LIB)
    seed_ms = round((time.perf_counter() - t_seed) * 1000, 1)

    port = free_port()
    env = {**os.environ, "HYDRA_URL": args.hydra_url, "PINNED_FACTORY": "server_fakes:build_services",
           "PYTHONPATH": str(HERE), "PINNED_MAINTAIN_EVERY": "0"}
    server = subprocess.Popen(
        [sys.executable, "-m", "pinned.cli", "serve", "--port", str(port), "--log-level", "warning"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{port}"
    t = Transcript()
    try:
        for _ in range(150):
            try:
                if httpx.get(f"{base}/healthz", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise RuntimeError("pinned serve did not come up")
        http_calls(base, t)
        asyncio.run(mcp_calls(base, env, t))
    finally:
        server.terminate()
        server.wait(10)
    head = (
        f"Live run {datetime.now(UTC).isoformat(timespec='seconds')} · HydraDB `ghcr.io/hydra-db/hydradb:0.4.0` at "
        f"{args.hydra_url} · seeded library `{LIB}` (3 versions, 3 APIs, 4 terms) in {seed_ms} ms · "
        f"`pinned serve` on {base} (separate process) · search/learn = test stand-ins over the real graph\n"
    )
    Path(args.out).write_text(head + "\n" + "\n".join(t.parts))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
