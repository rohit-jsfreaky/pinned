"""HydraDB v0.4.0 HTTP client: typed-cell decoding, full pagination, retries, a pooled thread-safe connection.

Behaviour is checked against the server (tests/graph) and the upstream guide USING-HYDRADB.md (hydra-db/hydradb).
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

PAGE_SIZE = 1000  # server default 1024, max 4096; must be resent identically on every page


class HydraError(RuntimeError):
    def __init__(self, code: str, message: str, query: str, status: int = 0):
        super().__init__(f"{code}: {message}\n  query: {query[:300]}")
        self.code, self.message, self.query, self.status = code, message, query, status


def decode(cell: Any) -> Any:
    """Typed cell -> plain value. Null cells have no "value" key; list cells hold typed cells; paths stay raw dicts."""
    if not isinstance(cell, dict) or "type" not in cell:
        return cell
    kind = cell["type"]
    if kind == "list":
        return [decode(c) for c in cell.get("value") or []]
    if kind == "path":
        return cell.get("value")
    return cell.get("value")


class HydraGraph:
    """`query()` returns every row (all pages) as dicts; `write()` runs a mutation. Safe to share across threads."""

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        graph: str | None = None,
        namespace: str | None = None,
        cell: str | None = None,
        timeout: float = 60.0,
        retries: int = 4,
        pool: int = 64,
    ):
        self.url = (url or os.environ.get("HYDRA_URL", "http://127.0.0.1:8443")).rstrip("/")
        self.graph = graph or os.environ.get("HYDRA_GRAPH", "default")
        self.cell = cell or os.environ.get("HYDRA_CELL", "cell-0")
        self.retries = retries
        self.requests = 0  # round trips made by this client (search reports graph_queries from it)
        token = token or os.environ.get("HYDRA_TOKEN", "local-development-token-32-bytes")
        self._http = httpx.Client(
            timeout=timeout,
            limits=httpx.Limits(max_connections=pool, max_keepalive_connections=pool),
            headers={
                "Authorization": f"Bearer {token}",
                "X-Graph-Namespace": namespace or os.environ.get("HYDRA_NAMESPACE", "default"),
                "Content-Type": "application/json",
            },
        )

    # ------------------------------------------------------------------ transport
    def _send(self, body: dict) -> dict:
        self.requests += 1
        r = self._http.post(f"{self.url}/v1/graphs/{self.graph}/query", json=body)
        try:
            data = r.json()
        except ValueError:
            # 422 = malformed body (bare framework string, neither "rows" nor "error")
            raise HydraError("http_" + str(r.status_code), r.text[:500], body.get("query", ""), r.status_code) from None
        if "error" in data:
            err = data["error"]
            raise HydraError(err.get("code", "error"), err.get("message", ""), body.get("query", ""), r.status_code)
        if r.status_code >= 400 or "rows" not in data:
            raise HydraError("http_" + str(r.status_code), str(data)[:500], body.get("query", ""), r.status_code)
        return data

    def _retrying(self, fn):
        """Retry transport failures and 5xx. Only used for idempotent calls (first page of a read, MERGE/UNWIND writes)."""
        delay = 0.2
        for attempt in range(self.retries + 1):
            try:
                return fn()
            except (httpx.TransportError, HydraError) as e:
                transient = isinstance(e, httpx.TransportError) or (isinstance(e, HydraError) and e.status >= 500)
                if not transient or attempt == self.retries:
                    raise
                time.sleep(delay)
                delay *= 2

    # ------------------------------------------------------------------ API
    def query(self, cypher: str, params: dict | None = None, page_size: int = PAGE_SIZE) -> list[dict]:
        """Run a read and return ALL rows as dicts keyed by column name.

        Cursor pages are consuming (a retried page is a skipped page), so on any failure mid-scan the whole scan restarts.
        """

        def scan() -> list[dict]:
            body = {"cell_id": self.cell, "query": cypher, "parameters": params or {}, "page_size": page_size}
            data = self._send(body)
            cols = data.get("columns") or []
            rows = list(data.get("rows") or [])
            while data.get("next_cursor") is not None:
                data = self._send({**body, "cursor": data["next_cursor"], "query_id": data["query_id"]})
                rows.extend(data.get("rows") or [])
            return [dict(zip(cols, (decode(c) for c in row))) for row in rows]

        return self._retrying(scan)

    def write(self, cypher: str, params: dict | None = None) -> None:
        """Run a mutation. Retried on transient errors: use MERGE / UNWIND batch forms (idempotent), never plain CREATE."""
        body = {"cell_id": self.cell, "query": cypher, "parameters": params or {}}
        self._retrying(lambda: self._send(body))

    def ready(self) -> bool:
        try:
            r = self._http.get(f"{self.url}/healthz", timeout=5)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> HydraGraph:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
