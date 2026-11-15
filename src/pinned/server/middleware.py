"""Pure-ASGI middleware (safe with streaming MCP responses): request timing headers and a per-client rate limit."""

from __future__ import annotations

import json
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class TokenBucket:
    """Per-key token bucket: `rate_per_min` sustained, bursts up to the same number. Thread-safe, bounded memory."""

    def __init__(self, rate_per_min: int, max_keys: int = 50_000, clock: Callable[[], float] = time.monotonic):
        self.capacity = float(rate_per_min)
        self.refill_per_s = rate_per_min / 60.0
        self.max_keys = max_keys
        self.clock = clock
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()  # key -> (tokens, last)
        self._lock = threading.Lock()

    def take(self, key: str) -> tuple[bool, int, float]:
        """-> (allowed, tokens left, seconds until one token is back)."""
        now = self.clock()
        with self._lock:
            tokens, last = self._buckets.pop(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.refill_per_s)
            allowed = tokens >= 1.0
            if allowed:
                tokens -= 1.0
            self._buckets[key] = (tokens, now)
            while len(self._buckets) > self.max_keys:
                self._buckets.popitem(last=False)
        wait = 0.0 if tokens >= 1.0 else (1.0 - tokens) / self.refill_per_s
        return allowed, int(tokens), wait


def client_key(scope: Scope, trust_proxy: bool) -> str:
    if trust_proxy:
        for name, value in scope.get("headers") or []:
            if name == b"x-forwarded-for":
                first = value.decode("latin-1").split(",")[0].strip()
                if first:
                    return first
    client = scope.get("client")
    return client[0] if client else "unknown"


class RateLimitMiddleware:
    """Limits requests whose path starts with one of `prefixes`. 429 with a JSON error and Retry-After when over."""

    def __init__(self, app: ASGIApp, rate_per_min: int, prefixes: tuple[str, ...] = ("/v1/", "/mcp"),
                 trust_proxy: bool = False, bucket: TokenBucket | None = None):
        self.app = app
        self.prefixes = prefixes
        self.trust_proxy = trust_proxy
        self.bucket = bucket or (TokenBucket(rate_per_min) if rate_per_min > 0 else None)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or self.bucket is None
            or scope.get("method") == "OPTIONS"
            or not scope["path"].startswith(self.prefixes)
        ):
            await self.app(scope, receive, send)
            return
        allowed, left, wait = self.bucket.take(client_key(scope, self.trust_proxy))
        limit_headers = [
            (b"x-ratelimit-limit", str(int(self.bucket.capacity)).encode()),
            (b"x-ratelimit-remaining", str(left).encode()),
        ]
        if not allowed:
            retry = max(1, math.ceil(wait))
            body = json.dumps({"error": {
                "code": "rate_limited",
                "message": f"too many requests; limit is {int(self.bucket.capacity)} per minute, retry in {retry}s",
                "details": {"retry_after_s": retry},
            }}).encode()
            await send({"type": "http.response.start", "status": 429, "headers": [
                (b"content-type", b"application/json"), (b"retry-after", str(retry).encode()),
                (b"content-length", str(len(body)).encode()), *limit_headers,
            ]})
            await send({"type": "http.response.body", "body": body})
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), *limit_headers]}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class TimingMiddleware:
    """Adds `Server-Timing: app;dur=<ms>` and `X-Response-Time-Ms` (time to the response head) to every response."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        t0 = time.perf_counter()

        async def timed(message: Message) -> None:
            if message["type"] == "http.response.start":
                ms = f"{(time.perf_counter() - t0) * 1000:.1f}"
                message = {**message, "headers": [
                    *message.get("headers", []),
                    (b"server-timing", f"app;dur={ms}".encode()),
                    (b"x-response-time-ms", ms.encode()),
                ]}
            await send(message)

        await self.app(scope, receive, timed)
