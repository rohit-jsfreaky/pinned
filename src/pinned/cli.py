"""`pinned` command line: serve the API + MCP, run the MCP server on stdio, and query the graph from a terminal.

Queries run against the local HydraDB graph (HYDRA_URL) by default, or against a running Pinned server with
--api URL (or PINNED_API), so a laptop without HydraDB can use a hosted instance.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol

import httpx

from pinned.contract import ApiTimeline, Context, ContextItem, Finding, Hop, VersionFact

EXIT_OK, EXIT_PROBLEMS, EXIT_INPUT, EXIT_UNAVAILABLE = 0, 1, 2, 3


class CliError(Exception):
    def __init__(self, message: str, exit_code: int = EXIT_INPUT, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message, self.exit_code, self.details = message, exit_code, details or {}


# ---------------------------------------------------------------------------------------------------- backends
class Backend(Protocol):
    def check(self, code: str, library: str, version: str) -> tuple[str, str, list[Finding]]: ...
    def how(self, task: str, library: str, version: str, code: str, k: int) -> Context: ...
    def explain(self, library: str, path: str) -> ApiTimeline: ...
    def stats(self) -> dict[str, Any]: ...
    def libraries(self) -> list[dict[str, Any]]: ...


def _hops(rows: list[dict]) -> list[Hop]:
    return [Hop(src=h["src"], rel=h["rel"], dst=h["dst"], note=h.get("note", "")) for h in rows or []]


class LocalBackend:
    """Talks to HydraDB directly through the same PinnedService the server uses."""

    def __init__(self) -> None:
        from pinned.server.errors import PinnedError
        from pinned.server.factory import service_from_env

        self._error_type = PinnedError
        self.service = self._guard(service_from_env)

    def _guard(self, fn, *args, **kwargs):
        from pinned.server.errors import GraphUnavailable, PinnedError, ServiceMisconfigured

        try:
            return fn(*args, **kwargs)
        except GraphUnavailable as e:
            url = os.environ.get("HYDRA_URL", "http://127.0.0.1:8443")
            raise CliError(
                f"{e.message}\n  HYDRA_URL={url}\n  Start a local node:  infra/hydradb-up.sh pinned 8443 7687 9090\n"
                "  or point HYDRA_URL at a running HydraDB, or use a Pinned server:  pinned --api https://<host> ...",
                EXIT_UNAVAILABLE,
            ) from e
        except ServiceMisconfigured as e:
            raise CliError(e.message, EXIT_UNAVAILABLE) from e
        except PinnedError as e:
            raise CliError(e.message, EXIT_UNAVAILABLE if e.status >= 500 else EXIT_INPUT, e.details) from e

    def check(self, code, library, version):
        return self._guard(self.service.check, code, library, version)

    def how(self, task, library, version, code, k):
        return self._guard(self.service.how, task, library, version, code=code, k=k)

    def explain(self, library, path):
        return self._guard(self.service.explain, library, path)

    def stats(self):
        return self._guard(self.service.stats)

    def libraries(self):
        return [asdict(li) for li in self._guard(self.service.list_libraries)]


class RemoteBackend:
    """Talks to a running Pinned server over its HTTP API."""

    def __init__(self, base_url: str, timeout: float = 60.0) -> None:
        self.base = base_url.rstrip("/")
        self.http = httpx.Client(base_url=self.base, timeout=timeout, headers={"User-Agent": "pinned-cli"})

    def _call(self, method: str, path: str, body: dict | None = None) -> dict[str, Any]:
        try:
            r = self.http.request(method, path, json=body)
        except httpx.HTTPError as e:
            raise CliError(f"cannot reach the Pinned server at {self.base} ({type(e).__name__}: {e})",
                           EXIT_UNAVAILABLE) from e
        try:
            data = r.json()
        except ValueError:
            raise CliError(f"{self.base}{path} answered {r.status_code} with non-JSON", EXIT_UNAVAILABLE) from None
        if r.status_code >= 400:
            err = data.get("error", {}) if isinstance(data, dict) else {}
            code = EXIT_UNAVAILABLE if r.status_code >= 500 or r.status_code == 429 else EXIT_INPUT
            raise CliError(err.get("message") or f"HTTP {r.status_code}", code, err.get("details"))
        return data

    def check(self, code, library, version):
        d = self._call("POST", "/v1/check", {"code": code, "library": library, "version": version})
        findings = [Finding(call=f["call"], status=f["status"], fix=f.get("fix", ""), chain=_hops(f.get("chain")),
                            line=f.get("line")) for f in d["findings"]]
        return d["library"], d["version"], findings

    def how(self, task, library, version, code, k):
        d = self._call("POST", "/v1/how", {"task": task, "library": library, "version": version, "code": code, "k": k})
        items = [ContextItem(library=i["library"], path=i["path"], status=i["status"], chain=_hops(i.get("chain")),
                             signature=i.get("signature", ""), note=i.get("note", ""), score=i.get("score", 0.0))
                 for i in d["items"]]
        return Context(library=d["library"], version=d["version"], items=items, query_ms=d.get("query_ms", 0.0),
                       graph_queries=d.get("graph_queries", 0))

    def explain(self, library, path):
        d = self._call("GET", f"/v1/api/{library}/{path}")
        return ApiTimeline(
            library=d["library"], path=d["path"], kind=d.get("kind", ""),
            versions=[VersionFact(version=v["version"], ord=v["ord"], status=v["status"],
                                  signature=v.get("signature", ""), note=v.get("note", "")) for v in d["versions"]],
            replaced_by=_hops(d.get("replaced_by")), graph_queries=d.get("graph_queries", 0),
        )

    def stats(self):
        return self._call("GET", "/v1/stats")

    def libraries(self):
        return self._call("GET", "/v1/libraries")["libraries"]


# ---------------------------------------------------------------------------------------------------- commands
def _read_code(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    p = Path(path)
    if not p.is_file():
        raise CliError(f"no such file: {path}")
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise CliError(f"cannot read {path}: {e}") from e


def _backend(args: argparse.Namespace) -> Backend:
    api = args.api or os.environ.get("PINNED_API", "")
    return RemoteBackend(api) if api else LocalBackend()


def _emit(args: argparse.Namespace, data: Any, text: str) -> None:
    if args.json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(text)


def cmd_check(args: argparse.Namespace) -> int:
    from pinned.server.format import findings_text

    code = _read_code(args.file)
    lib, ver, findings = _backend(args).check(code, args.lib, args.version)
    ok = all(f.status == "valid" for f in findings)
    _emit(args, {"library": lib, "version": ver, "ok": ok, "findings": [asdict(f) for f in findings]},
          findings_text(lib, ver, findings))
    return EXIT_OK if ok else EXIT_PROBLEMS


def cmd_how(args: argparse.Namespace) -> int:
    from pinned.server.format import context_text

    code = _read_code(args.code) if args.code else ""
    ctx = _backend(args).how(args.task, args.lib, args.version, code, args.k)
    _emit(args, asdict(ctx), context_text(ctx))
    return EXIT_OK


def cmd_explain(args: argparse.Namespace) -> int:
    from pinned.server.format import timeline_text

    tl = _backend(args).explain(args.library, args.path)
    _emit(args, asdict(tl), timeline_text(tl))
    return EXIT_OK


def cmd_stats(args: argparse.Namespace) -> int:
    s = _backend(args).stats()
    g = s.get("graph", {})
    lines = [f"graph: {g.get('total_nodes', 0)} nodes, {g.get('total_edges', 0)} edges"]
    lines += [f"  {k}: {v}" for k, v in (g.get("nodes") or {}).items()]
    lines += [f"  -{k}->: {v}" for k, v in (g.get("edges") or {}).items()]
    for part, summary in (s.get("latency") or {}).items():
        if not summary:
            lines.append(f"latency ({part}): no saved run yet")
            continue
        lines.append(f"latency ({part}) from {summary['file']}:")
        for name, recs in summary["latency"].items():
            cells = ", ".join(
                f"c={r.get('concurrency', '?')} p50 {r.get('p50_ms')}ms p95 {r.get('p95_ms')}ms" for r in recs
            )
            lines.append(f"  {name}: {cells}")
    _emit(args, s, "\n".join(lines))
    return EXIT_OK


def cmd_libraries(args: argparse.Namespace) -> int:
    libs = _backend(args).libraries()
    lines = [f"{li['name']}: " + ", ".join(v["version"] for v in li["versions"]) for li in libs]
    _emit(args, libs, "\n".join(lines) if lines else "the graph has no libraries yet")
    return EXIT_OK


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from pinned.server.factory import app_from_env
    from pinned.server.errors import PinnedError

    try:
        app = app_from_env()
    except PinnedError as e:
        raise CliError(e.message, EXIT_UNAVAILABLE) from e
    try:
        app.state.service.health()
    except PinnedError as e:
        print(f"warning: {e.message}; /healthz will report 503 until HydraDB answers", file=sys.stderr)
    uvicorn.run(app, host=args.host, port=args.port, proxy_headers=True, log_level=args.log_level)
    return EXIT_OK


def cmd_mcp(args: argparse.Namespace) -> int:
    from pinned.server.errors import PinnedError
    from pinned.server.factory import service_from_env
    from pinned.server.mcp_server import build_mcp

    # stdout is the MCP channel: every log line goes to stderr.
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    try:
        service = service_from_env()
    except PinnedError as e:
        raise CliError(e.message, EXIT_UNAVAILABLE) from e
    build_mcp(service).run("stdio")
    return EXIT_OK


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="print JSON instead of text")
    common.add_argument("--api", default=argparse.SUPPRESS, metavar="URL",
                        help="use a running Pinned server instead of HydraDB directly (or set PINNED_API)")

    p = argparse.ArgumentParser(
        prog="pinned",
        description="Is this code valid for the library version you actually have? Version-correct API facts from the "
                    "Pinned graph.",
        parents=[common],
    )
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    s = sub.add_parser("serve", help="run the HTTP API with the MCP endpoint at /mcp")
    s.add_argument("--host", default=os.environ.get("PINNED_HOST", "127.0.0.1"))
    s.add_argument("--port", type=int, default=int(os.environ.get("PINNED_PORT", "8000")))
    s.add_argument("--log-level", default="info", choices=["critical", "error", "warning", "info", "debug"])
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("mcp", help="run the MCP server on stdio (for `claude mcp add`, Codex, Cursor)")
    s.set_defaults(fn=cmd_mcp)

    s = sub.add_parser("check", parents=[common], help="check a file's calls against a library version")
    s.add_argument("file", help="Python file to check ('-' reads stdin)")
    s.add_argument("--lib", required=True, help="library (PyPI name), e.g. pandas")
    s.add_argument("--version", required=True, help="installed version, e.g. 2.0.3")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("how", parents=[common], help="API facts for a task at a library version")
    s.add_argument("task", help="what the code must do, in plain words")
    s.add_argument("--lib", required=True)
    s.add_argument("--version", required=True)
    s.add_argument("--code", help="optional file with your current code")
    s.add_argument("-k", type=int, default=8, help="number of facts (default 8)")
    s.set_defaults(fn=cmd_how)

    s = sub.add_parser("explain", parents=[common], help="one API's history across versions")
    s.add_argument("library")
    s.add_argument("path", help="dotted import path, e.g. pandas.DataFrame.append")
    s.set_defaults(fn=cmd_explain)

    s = sub.add_parser("stats", parents=[common], help="graph size and saved latency numbers")
    s.set_defaults(fn=cmd_stats)

    s = sub.add_parser("libraries", parents=[common], help="libraries and versions in the graph")
    s.set_defaults(fn=cmd_libraries)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.json = getattr(args, "json", False)
    args.api = getattr(args, "api", "")
    try:
        return args.fn(args)
    except CliError as e:
        if args.json:
            print(json.dumps({"error": {"message": e.message, "details": e.details}}, indent=2, default=str))
        else:
            print(f"pinned: {e.message}", file=sys.stderr)
            for k, v in e.details.items():
                if v not in (None, [], ""):
                    print(f"  {k}: {v}", file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
