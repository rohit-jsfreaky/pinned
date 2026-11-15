"""The Pinned HTTP API (FastAPI) with the MCP Streamable HTTP endpoint mounted at /mcp on the same app.

`create_app(search, learner, graph, settings)` takes ready objects (dependency injection: tests pass fakes, Phase 2 passes
the real parts); `pinned.server.factory.app_from_env()` builds them from the environment.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mcp.server.transport_security import TransportSecuritySettings
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from pinned.contract import Graph, Learner, Search

from .config import Settings
from .errors import PinnedError
from .mcp_server import build_mcp
from .middleware import RateLimitMiddleware, TimingMiddleware
from .models import (
    CheckIn,
    CheckOut,
    ErrorOut,
    HowIn,
    HowOut,
    LibrariesOut,
    OutcomeIn,
    OutcomeOut,
    TimelineOut,
    check_out,
    how_out,
    libraries_out,
    outcome_out,
    timeline_out,
)
from .service import PinnedService

log = logging.getLogger("pinned.server")

ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorOut, "description": "Library, version or API not in the graph"},
    422: {"model": ErrorOut, "description": "Invalid input"},
    429: {"model": ErrorOut, "description": "Rate limited"},
    502: {"model": ErrorOut, "description": "HydraDB rejected a query"},
    503: {"model": ErrorOut, "description": "HydraDB unreachable"},
}


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


def _error(status: int, code: str, message: str, details: dict[str, Any] | None = None,
           headers: dict[str, str] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message}
    if details:
        body["details"] = details
    return JSONResponse({"error": body}, status_code=status, headers=headers)


def _transport_security(settings: Settings) -> TransportSecuritySettings | None:
    """Host allowlist for /mcp. None = the SDK's localhost-only default. "*" turns the Host check off (only behind a
    reverse proxy that already pins the Host)."""
    if not settings.allowed_hosts:
        return None
    if "*" in settings.allowed_hosts:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    hosts = [*settings.allowed_hosts, *(f"{h}:*" for h in settings.allowed_hosts if ":" not in h)]
    hosts += ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    origins = [o for o in settings.cors_origins if o != "*"]
    origins += ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins)


def create_app(search: Search, learner: Learner, graph: Graph, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    service = PinnedService(search, learner, graph, settings)
    mcp = build_mcp(service)
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,  # no per-client session state: any replica can answer any request
        json_response=True,
        transport_security=_transport_security(settings),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # A mounted app's own lifespan never runs, so the host app enters the MCP session manager.
        async with mcp.session_manager.run():
            yield

    app = FastAPI(
        title="Pinned",
        version=__version__,
        summary="Is this code valid for the library version you actually have? Version-correct API facts for coding "
                "agents, from a self-improving HydraDB graph.",
        lifespan=lifespan,
        responses=ERRORS,
    )
    app.state.service = service
    app.state.mcp = mcp

    # ------------------------------------------------------------------ errors -> one JSON shape
    @app.exception_handler(PinnedError)
    async def pinned_error(_: Request, e: PinnedError) -> JSONResponse:
        if e.status >= 500:
            log.warning("%s: %s", e.code, e.message)
        return _error(e.status, e.code, e.message, e.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, e: RequestValidationError) -> JSONResponse:
        problems = [
            {"field": ".".join(str(p) for p in err.get("loc", ()) if p != "body"), "problem": err.get("msg", "")}
            for err in e.errors()
        ]
        first = problems[0] if problems else {"field": "", "problem": "invalid request"}
        return _error(422, "bad_input", f"{first['field'] or 'request'}: {first['problem']}", {"problems": problems})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, e: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(e.status_code, "http_error")
        return _error(e.status_code, code, str(e.detail), headers=getattr(e, "headers", None))

    @app.exception_handler(Exception)
    async def crash(_: Request, e: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return _error(500, "internal_error", "internal error; see the server log")

    # ------------------------------------------------------------------ routes (sync defs run in the threadpool)
    @app.post("/v1/check", response_model=CheckOut, tags=["agent"])
    def check(body: CheckIn) -> CheckOut:
        """Check every call to the library in `code` against `version`: status, fix and evidence chain per call."""
        t0 = time.perf_counter()
        lib, ver, findings = service.check(body.code, body.library, body.version)
        return check_out(lib, ver, findings, _ms(t0))

    @app.post("/v1/how", response_model=HowOut, tags=["agent"])
    def how(body: HowIn) -> HowOut:
        """The API facts for doing `task` at `version` (items + evidence chains + a ready prompt block)."""
        t0 = time.perf_counter()
        ctx = service.how(body.task, body.library, body.version, code=body.code, k=body.k)
        return how_out(ctx, _ms(t0))

    @app.get("/v1/api/{library}/{path:path}", response_model=TimelineOut, tags=["agent"])
    def explain(library: str, path: str) -> TimelineOut:
        """Timeline of one API across every ingested version, plus what replaced it."""
        t0 = time.perf_counter()
        tl = service.explain(library, path)
        return timeline_out(tl, _ms(t0))

    @app.post("/v1/outcome", response_model=OutcomeOut, tags=["agent"])
    def outcome(body: OutcomeIn) -> OutcomeOut:
        """Record whether the agent's code passed; the learner updates the graph from it (run_id "live")."""
        t0 = time.perf_counter()
        res = service.outcome(body.code, body.library, body.version, body.passed, body.error_type, body.error_text,
                              body.task)
        return outcome_out(res, _ms(t0))

    @app.get("/v1/libraries", response_model=LibrariesOut, tags=["graph"])
    def libraries() -> LibrariesOut:
        """Every library in the graph with its ingested versions (oldest first)."""
        t0 = time.perf_counter()
        return libraries_out(service.list_libraries(), _ms(t0))

    @app.get("/v1/stats", tags=["graph"])
    def stats() -> dict[str, Any]:
        """Graph size (nodes per label, edges per type) and the latest saved latency numbers (with their files)."""
        t0 = time.perf_counter()
        return {**service.stats(), "took_ms": _ms(t0)}

    @app.get("/v1/curve", tags=["graph"])
    def curve() -> dict[str, Any]:
        """The learning curve from evidence/curve.json (404 until the evaluation run has written it)."""
        return service.curve()

    @app.get("/healthz", tags=["ops"])
    def healthz() -> JSONResponse:
        """200 when the server and HydraDB both answer; 503 otherwise."""
        try:
            return JSONResponse({**service.health(), "version": __version__})
        except PinnedError as e:
            return JSONResponse(
                {"status": "degraded", "version": __version__, "hydradb": {"ok": False, "error": e.message}},
                status_code=503,
            )

    # /mcp: reuse the SDK's own route (keeps FastAPI's JSON 404s for every other path, unlike a catch-all mount).
    app.router.routes.extend(mcp_app.routes)

    # Middleware: last added = outermost. Timing wraps everything; CORS answers preflights before the rate limit.
    app.add_middleware(RateLimitMiddleware, rate_per_min=settings.rate_limit_per_min, trust_proxy=settings.trust_proxy)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "Authorization", "Mcp-Session-Id", "Mcp-Protocol-Version",
                           "Last-Event-ID", "Accept"],
            expose_headers=["Mcp-Session-Id", "Server-Timing", "X-Response-Time-Ms", "X-RateLimit-Limit",
                            "X-RateLimit-Remaining", "Retry-After"],
            max_age=600,
        )
    app.add_middleware(TimingMiddleware)
    return app
