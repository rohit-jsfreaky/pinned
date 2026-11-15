"""Command line for search.

    uv run python -m pinned.search how "task text" --lib pandas --version 1.3.5 [--code file.py] [-k 8]
    uv run python -m pinned.search check file.py --lib pandas --version 2.0.3
    uv run python -m pinned.search explain pandas pandas.DataFrame.append
Add --json for machine output. The graph is HYDRA_URL (default http://127.0.0.1:8443) or --url.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

import httpx

from pinned.contract import ApiTimeline, Context, Finding, Hop
from pinned.graph import HydraError, HydraGraph

from .search import GraphSearch


def _chain(chain: list[Hop]) -> str:
    return " ; ".join(f"{h.src} -{h.rel}-> {h.dst}" + (f" ({h.note})" if h.note else "") for h in chain)


def format_context(ctx: Context) -> str:
    lines = [f"{ctx.library}=={ctx.version}: {len(ctx.items)} item(s) in {ctx.query_ms:.1f} ms, "
             f"{ctx.graph_queries} graph queries"]
    for n, it in enumerate(ctx.items, 1):
        lines.append(f"{n}. {it.path}  [{it.status}]  score {it.score:.3f}")
        if it.signature:
            lines.append(f"   signature: {it.signature}")
        if it.note:
            lines.append(f"   {it.note}")
        if it.chain:
            lines.append(f"   chain: {_chain(it.chain)}")
    return "\n".join(lines)


def format_findings(findings: list[Finding]) -> str:
    if not findings:
        return "No calls into this library found in the code."
    lines = []
    for f in findings:
        where = f"line {f.line}: " if f.line else ""
        lines.append(f"{where}{f.call}  [{f.status}]" + (f"  fix: {f.fix}" if f.fix else ""))
        if f.chain:
            lines.append(f"   chain: {_chain(f.chain)}")
    return "\n".join(lines)


def format_timeline(t: ApiTimeline) -> str:
    if not t.versions:
        return f"Pinned has no data for the library '{t.library}'."
    lines = [f"{t.path} ({t.kind or 'unknown kind'}) in {t.library}, {t.graph_queries} graph queries"]
    width = max(len(v.version) for v in t.versions)
    for v in t.versions:
        sig = f"  {v.signature}" if v.signature else ""
        note = f"  <- {v.note}" if v.note else ""
        lines.append(f"  {v.version:<{width}}  {v.status:<11}{sig}{note}")
    if t.replaced_by:
        lines.append(f"chain: {_chain(t.replaced_by)}")
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m pinned.search", description="Version-aware API facts from the graph.")
    ap.add_argument("--url", default=None, help="HydraDB HTTP url (default: $HYDRA_URL or http://127.0.0.1:8443)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    sub = ap.add_subparsers(dest="cmd", required=True)
    how = sub.add_parser("how", help="context for a task at a library version")
    how.add_argument("task")
    how.add_argument("--lib", required=True)
    how.add_argument("--version", required=True)
    how.add_argument("--code", type=Path, help="file with the starting code")
    how.add_argument("-k", type=int, default=8)
    chk = sub.add_parser("check", help="check every library call in a file against a version")
    chk.add_argument("file", type=Path)
    chk.add_argument("--lib", required=True)
    chk.add_argument("--version", required=True)
    exp = sub.add_parser("explain", help="an API's status and signature across every ingested version")
    exp.add_argument("library")
    exp.add_argument("path")
    for p in (how, chk, exp):  # accept --json after the sub-command too
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    return ap


def main(argv: list[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    try:
        with HydraGraph(url=a.url) as g:
            s = GraphSearch(g)
            try:
                if a.cmd == "how":
                    code = a.code.read_text() if a.code else ""
                    ctx = s.context_for(a.task, code, a.lib, a.version, a.k)
                    out = json.dumps(asdict(ctx), indent=2) if a.json else format_context(ctx)
                elif a.cmd == "check":
                    findings = s.check_calls(a.file.read_text(), a.lib, a.version)
                    out = json.dumps([asdict(f) for f in findings], indent=2) if a.json else format_findings(findings)
                else:
                    t = s.explain_api(a.library, a.path)
                    out = json.dumps(asdict(t), indent=2) if a.json else format_timeline(t)
            finally:
                s.close()
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except HydraError as e:
        print(f"graph error: {e}", file=sys.stderr)
        return 3
    except httpx.HTTPError as e:  # the node is down or the url is wrong
        print(f"cannot reach the graph: {e}", file=sys.stderr)
        return 3
    print(out)
    return 0
