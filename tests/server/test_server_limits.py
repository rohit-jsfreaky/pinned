"""Per-client rate limit (middleware + token bucket) and CORS."""

from __future__ import annotations

from fastapi.testclient import TestClient
from server_fakes import GraphBackedSearch, RecordingLearner

from pinned.server import Settings, create_app
from pinned.server.middleware import TokenBucket


def _app(graph, tmp_path, **kw):
    s = Settings(evidence_dir=tmp_path, maintain_every=0, **kw)
    return create_app(GraphBackedSearch(graph), RecordingLearner(graph), graph, s)


def test_rate_limit_429_with_retry_after(graph, lib, tmp_path):
    with TestClient(_app(graph, tmp_path, rate_limit_per_min=3)) as c:
        codes = [c.get("/v1/libraries").status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]
        r = c.get("/v1/libraries")
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "rate_limited" and int(r.headers["retry-after"]) >= 1
        assert r.headers["x-ratelimit-limit"] == "3" and r.headers["x-ratelimit-remaining"] == "0"
        assert c.get("/healthz").status_code == 200  # health checks are never limited


def test_rate_limit_is_per_client_behind_trusted_proxy(graph, lib, tmp_path):
    with TestClient(_app(graph, tmp_path, rate_limit_per_min=1, trust_proxy=True)) as c:
        assert c.get("/v1/libraries", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
        assert c.get("/v1/libraries", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 429
        assert c.get("/v1/libraries", headers={"X-Forwarded-For": "2.2.2.2, 10.0.0.1"}).status_code == 200


def test_forwarded_for_ignored_without_trust(graph, lib, tmp_path):
    with TestClient(_app(graph, tmp_path, rate_limit_per_min=1)) as c:
        assert c.get("/v1/libraries", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
        assert c.get("/v1/libraries", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 429


def test_rate_limit_off(graph, lib, tmp_path):
    with TestClient(_app(graph, tmp_path, rate_limit_per_min=0)) as c:
        assert all(c.get("/v1/libraries").status_code == 200 for _ in range(5))


def test_token_bucket_refills():
    now = [0.0]
    b = TokenBucket(60, clock=lambda: now[0])  # 1 token per second
    assert all(b.take("a")[0] for _ in range(60))
    allowed, left, wait = b.take("a")
    assert not allowed and left == 0 and 0 < wait <= 1.0
    now[0] += 1.0
    assert b.take("a")[0] and not b.take("a")[0]
    assert b.take("b")[0]  # other clients unaffected


def test_token_bucket_memory_is_bounded():
    b = TokenBucket(10, max_keys=100)
    for i in range(1000):
        b.take(str(i))
    assert len(b._buckets) == 100


def test_cors_allowed_origin_and_preflight(graph, lib, tmp_path):
    with TestClient(_app(graph, tmp_path, cors_origins=["https://app.example.com"])) as c:
        r = c.get("/v1/libraries", headers={"Origin": "https://app.example.com"})
        assert r.headers["access-control-allow-origin"] == "https://app.example.com"
        assert "x-response-time-ms" in r.headers["access-control-expose-headers"].lower()
        pre = c.options("/v1/check", headers={"Origin": "https://app.example.com",
                                              "Access-Control-Request-Method": "POST",
                                              "Access-Control-Request-Headers": "content-type"})
        assert pre.status_code == 200 and "POST" in pre.headers["access-control-allow-methods"]
        bad = c.get("/v1/libraries", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in bad.headers


def test_cors_off_by_default(graph, lib, tmp_path):
    with TestClient(_app(graph, tmp_path)) as c:
        r = c.get("/v1/libraries", headers={"Origin": "https://app.example.com"})
        assert "access-control-allow-origin" not in r.headers


def test_preflight_not_rate_limited(graph, lib, tmp_path):
    with TestClient(_app(graph, tmp_path, rate_limit_per_min=1, cors_origins=["*"])) as c:
        hdrs = {"Origin": "https://x.example", "Access-Control-Request-Method": "POST"}
        assert all(c.options("/v1/check", headers=hdrs).status_code == 200 for _ in range(3))


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("PINNED_CORS_ORIGINS", "https://a.example, https://b.example")
    monkeypatch.setenv("PINNED_RATE_LIMIT", "7")
    monkeypatch.setenv("PINNED_TRUST_PROXY", "true")
    monkeypatch.setenv("HYDRA_URL", "http://h:1")
    s = Settings.from_env()
    assert s.cors_origins == ["https://a.example", "https://b.example"]
    assert (s.rate_limit_per_min, s.trust_proxy, s.hydra_url) == (7, True, "http://h:1")
    monkeypatch.setenv("PINNED_RATE_LIMIT", "lots")
    try:
        Settings.from_env()
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "PINNED_RATE_LIMIT" in str(e)
