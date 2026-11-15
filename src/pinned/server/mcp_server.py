"""The Pinned MCP server (official MCP Python SDK, MCPServer). Four tools for coding agents; each returns compact text the
agent can act on directly (one line per item, evidence chain on the line) plus the same data as structured content.

Transports: stdio (`pinned mcp`) and Streamable HTTP, mounted at /mcp on the HTTP app (see http_app.create_app).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Annotated, Any, TypeVar

import anyio.to_thread
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from . import format as fmt
from .errors import PinnedError
from .models import CheckOut, HowOut, OutcomeOut, TimelineOut, check_out, how_out, outcome_out, timeline_out
from .service import PinnedService

T = TypeVar("T")

INSTRUCTIONS = """\
Pinned knows which Python library APIs exist at which version (added, removed, renamed, signature changed, replaced
by), as a graph built from the real released packages, and it learns from reported outcomes.
Use it whenever you write or fix code for a library at a specific installed version (check requirements.txt,
pyproject.toml, uv.lock or `pip show <lib>` for the version):
1. how_to before writing code: the right calls for that exact version, with evidence chains.
2. check_code after writing code: flags calls that are removed, renamed, not yet added or changed at that version,
   with the fix.
3. explain_api when you are unsure about one function/class: its full history across versions.
4. report_outcome after you run the code (pass or fail, with the error): this is how Pinned gets better.
Statuses: valid, missing, removed, renamed, changed, added_later, deprecated, unknown.
"""

Lib = Annotated[str, Field(description="PyPI project name exactly as installed, e.g. pandas, scikit-learn, torch")]
Ver = Annotated[str, Field(description="The installed version of that library, e.g. 2.0.3 (not a range)")]
Code = Annotated[str, Field(description="The Python source code (a file or snippet) that uses the library")]

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)


def _result(text: str, model: Any) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=model.model_dump(mode="json"))


def build_mcp(service: PinnedService) -> MCPServer:
    mcp = MCPServer(
        "pinned",
        title="Pinned: version-correct library APIs",
        instructions=INSTRUCTIONS,
        website_url="https://github.com/rohit-jsfreaky/pinned",
    )

    async def call(fn: Callable[[], T]) -> T:
        """Run a blocking service call off the event loop; turn product errors into tool errors the agent can read."""
        try:
            return await anyio.to_thread.run_sync(fn)
        except PinnedError as e:
            hint = ""
            if e.details:
                hint = " " + "; ".join(f"{k}: {v}" for k, v in e.details.items() if v not in (None, [], ""))
            raise ToolError(f"{e.code}: {e.message}.{hint}") from e

    @mcp.tool(
        title="Check code against a library version",
        annotations=READ_ONLY,
        description=(
            "Check every call to `library` in `code` against the exact installed `version`. Call this after you write "
            "or edit code that uses a version-pinned library, and before running it. Returns one line per call: its "
            "status at that version (valid / removed / renamed / changed / added_later / deprecated / missing), the "
            "fix to use instead, and the graph evidence chain (e.g. `pandas.DataFrame.append -REMOVED_IN-> pandas 2.0 "
            "; ... -REPLACED_BY-> pandas.concat`). `ok` is true only when every call is valid."
        ),
    )
    async def check_code(code: Code, library: Lib, version: Ver) -> Annotated[CallToolResult, CheckOut]:
        t0 = time.perf_counter()
        lib, ver, findings = await call(lambda: service.check(code, library, version))
        out = check_out(lib, ver, findings, round((time.perf_counter() - t0) * 1000, 1))
        return _result(fmt.findings_text(lib, ver, findings), out)

    @mcp.tool(
        title="How to do a task at a library version",
        annotations=READ_ONLY,
        description=(
            "Get the API facts for doing `task` with `library` at the exact installed `version`: which functions/classes "
            "to use, their signature at that version, what to avoid (removed/renamed calls) and why, as evidence chains "
            "from the Pinned graph. Call this BEFORE writing code for a pinned version, especially for older or newer "
            "versions than you know well. Pass your current `code` if you have some. Returns up to `k` facts, one per line."
        ),
    )
    async def how_to(
        task: Annotated[str, Field(description="What the code must do, in plain words (the problem statement)")],
        library: Lib,
        version: Ver,
        code: Annotated[str, Field(description="Optional: your current code, so its calls are considered")] = "",
        k: Annotated[int, Field(description="How many facts to return (1-32)", ge=1, le=32)] = 8,
    ) -> Annotated[CallToolResult, HowOut]:
        t0 = time.perf_counter()
        ctx = await call(lambda: service.how(task, library, version, code=code, k=k))
        out = how_out(ctx, round((time.perf_counter() - t0) * 1000, 1))
        return _result(fmt.context_text(ctx), out)

    @mcp.tool(
        title="Explain one API across versions",
        annotations=READ_ONLY,
        description=(
            "Show the history of one API (`path`, a dotted import path like `scipy.misc.comb` or "
            "`pandas.DataFrame.append`) across every ingested version of `library`: when it was added, changed "
            "signature, was deprecated or removed, and what replaced it. Call this when you are unsure whether a "
            "specific function/class/argument exists at your version or what to use instead."
        ),
    )
    async def explain_api(
        library: Lib,
        path: Annotated[str, Field(description="Dotted import path, e.g. pandas.DataFrame.append")],
    ) -> Annotated[CallToolResult, TimelineOut]:
        t0 = time.perf_counter()
        tl = await call(lambda: service.explain(library, path))
        out = timeline_out(tl, round((time.perf_counter() - t0) * 1000, 1))
        return _result(fmt.timeline_text(tl), out)

    @mcp.tool(
        title="Report whether code worked",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                                    open_world_hint=False),
        description=(
            "Report the outcome of running code for `library`==`version`: `passed` true/false, and on failure the "
            "`error_type` (AttributeError, ImportError, TypeError, ...) and `error_text` (the trimmed error message). "
            "Call this every time you run code you wrote with Pinned's help, pass or fail, and pass the same `task` you "
            "gave how_to. Pinned learns from it (strengthens facts that worked, weakens ones that failed, and fills "
            "gaps the error reveals), so later answers get better. Never include secrets in error_text."
        ),
    )
    async def report_outcome(
        code: Code,
        library: Lib,
        version: Ver,
        passed: Annotated[bool, Field(description="True if the code ran and its tests passed")],
        error_type: Annotated[str, Field(description="Exception class name on failure, e.g. AttributeError")] = "",
        error_text: Annotated[str, Field(description="Trimmed error message / last traceback lines on failure")] = "",
        task: Annotated[str, Field(description="The task the code was written for (same text as given to how_to)")] = "",
    ) -> Annotated[CallToolResult, OutcomeOut]:
        t0 = time.perf_counter()
        res = await call(lambda: service.outcome(code, library, version, passed, error_type, error_text, task))
        out = outcome_out(res, round((time.perf_counter() - t0) * 1000, 1))
        r = res.report
        text = (
            f"Recorded {'pass' if passed else 'fail'} for {library}=={version} as {res.episode_id}. "
            f"Learned: {r.edges_reweighted} edges reweighted, {r.shortcuts_added} shortcuts, {r.gaps_ingested} gaps "
            f"ingested, {r.merged} merged, {r.split} split."
            + (" " + " ".join(r.notes) if r.notes else "")
        )
        return _result(text, out)

    return mcp
