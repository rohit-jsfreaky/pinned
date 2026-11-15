"""Typed errors shared by the HTTP API, the MCP tools and the CLI (each renders them its own way)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx

from pinned.graph import HydraError


class PinnedError(Exception):
    status = 500
    code = "internal_error"

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            body["details"] = self.details
        return body


class BadInput(PinnedError):
    status = 422
    code = "bad_input"


class NotFound(PinnedError):
    status = 404
    code = "not_found"


class RateLimited(PinnedError):
    status = 429
    code = "rate_limited"


class GraphUnavailable(PinnedError):
    """HydraDB cannot be reached (down, wrong HYDRA_URL, network)."""

    status = 503
    code = "graph_unavailable"


class GraphFailed(PinnedError):
    """HydraDB answered with an error for a query we sent."""

    status = 502
    code = "graph_error"


class EvidenceUnreadable(PinnedError):
    status = 500
    code = "evidence_unreadable"


class ServiceMisconfigured(PinnedError):
    """The configured factory could not build the search / learner / graph objects."""

    status = 500
    code = "misconfigured"


@contextmanager
def graph_errors(url: str = "") -> Iterator[None]:
    """Turn transport / HydraDB failures raised anywhere inside (search, learner, helpers) into typed errors."""
    try:
        yield
    except httpx.TransportError as e:
        where = f" at {url}" if url else ""
        raise GraphUnavailable(f"HydraDB is not reachable{where} ({type(e).__name__})") from e
    except HydraError as e:
        if e.status >= 500 or e.code.startswith("http_5"):
            raise GraphUnavailable(f"HydraDB is failing: {e.code}: {e.message[:200]}") from e
        raise GraphFailed(f"HydraDB rejected a query: {e.code}: {e.message[:200]}") from e
