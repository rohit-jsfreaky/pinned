# Phase 1 · F — product server (HTTP API, MCP server, CLI)

Branch: `claude/relaxed-keller-38xtcp`. Status: **all 6 scope items done, tested, and run.** Whole suite: `74 passed`
(15 graph + 59 server) with `uv run pytest -q`.

## What is done (per scope item)

### 1. HTTP API (FastAPI) — `src/pinned/server/http_app.py`
- `POST /v1/check {code, library, version}` → `{library, version, ok, findings[{call, status, fix, chain, line}], took_ms}`
- `POST /v1/how {task, code?, library, version, k?}` → `{items[...], prompt, query_ms, graph_queries, took_ms}`
  (`prompt` = `Context.to_prompt()`, ready to paste)
- `GET /v1/api/{library}/{path}` → API timeline `{versions[{version, ord, status, signature, note}], replaced_by, ...}`
- `POST /v1/outcome {code, library, version, passed, error_type?, error_text?, task?}` → builds an `Episode`
  (run_id `live`, problem_id `live-<sha1 of lib|ver|task|code>`), gives it the context that `/v1/how` returned for the
  same (library, version, task) if still in memory (LRU of 1024), else a fresh `context_for`, else none; then
  `learner.record()` under a lock. Every `PINNED_MAINTAIN_EVERY` outcomes (default 50) runs `learner.maintain()` once in
  a background thread (single-flight).
- `GET /v1/libraries` → libraries + versions from the graph (oldest first).
- `GET /v1/stats` → nodes per label, edges per type (`count_nodes` / `count_edges`), plus the latest bench file in
  `evidence/load/` and `evidence/search/` (every `p50_ms` record found, with its file path; nothing invented). Cached 15 s.
- `GET /v1/curve` → `evidence/curve.json`; clear 404 while it does not exist; 500 `evidence_unreadable` if broken.
- `GET /healthz` → checks HydraDB `/healthz` AND a real query; 200 `ok` or 503 `degraded`.
- One error shape everywhere: `{"error": {"code", "message", "details"}}` (codes: `bad_input` 422, `not_found` 404,
  `rate_limited` 429, `graph_error` 502, `graph_unavailable` 503, `evidence_unreadable` / `internal_error` 500).
- Version resolution: `2.0` = `2.0.0` (compared by `version_ord`); library name is case-insensitive. Unknown library →
  404 with known libraries; unknown version → 404 with `known_versions`, `nearest_lower`, `nearest_higher`.
- Input limits: code ≤ `PINNED_MAX_CODE_BYTES` (100 000 bytes), `1 ≤ k ≤ PINNED_MAX_K` (32), task ≤ 4000 chars,
  error_text ≤ 20 000, path must be a dotted identifier ≤ 300, unknown JSON fields rejected. Input errors are reported
  before any graph call (so they stay 422 even when HydraDB is down).
- Per-client rate limit (pure ASGI token bucket, `PINNED_RATE_LIMIT`/min, default 120, burst = same; 429 +
  `Retry-After`, `X-RateLimit-*`; `/healthz` and CORS preflights are never limited; `X-Forwarded-For` used only with
  `PINNED_TRUST_PROXY=1`; memory bounded to 50k clients).
- CORS from `PINNED_CORS_ORIGINS` (comma list, `*` allowed; off when unset), exposes timing / rate / `Mcp-Session-Id`.
- Request timing: `Server-Timing: app;dur=…` and `X-Response-Time-Ms` headers on every response, `took_ms` in bodies.
- Sync route handlers (run in the threadpool), so blocking HydraDB calls never stall the event loop.
- Code layout: `service.py` (one `PinnedService` used by HTTP, MCP and CLI: validation, version resolution, error
  mapping, learning hooks), `models.py`, `errors.py`, `middleware.py`, `evidence.py`, `format.py`, `config.py`,
  `factory.py`. Dependency injection: `create_app(search, learner, graph, settings)`; `factory.app_from_env()` builds
  the real objects (`GraphSearch(graph)`, `GraphLearner(graph, ingestor=PyPIIngestor(graph, cache_dir=…), run_id="live")`,
  `HydraGraph(HYDRA_URL)`). `PINNED_FACTORY=module:callable` swaps the builder (used by the live run and CLI tests).

### 2. MCP server — `src/pinned/server/mcp_server.py` (official SDK `mcp` 2.3.0, `MCPServer`)
- Read the current v2 docs first (What's new, Structured output, Add to an existing app, Testing) and the installed
  source. v2 notes that matter: `MCPServer` replaces FastMCP; transport options moved to `run()` /
  `streamable_http_app()`; a mounted app's lifespan never runs, so the host app enters `mcp.session_manager.run()`;
  the HTTP app is localhost-only unless `transport_security` allows the host (else 421).
- Tools `check_code`, `how_to`, `explain_api`, `report_outcome`, with descriptions written for agents (when to call,
  what comes back), server `instructions` describing the workflow, tool annotations (read-only for the first three).
- Each tool returns `Annotated[CallToolResult, <pydantic model>]`: compact text (one line per item with the chain on
  that line) + the same data as `structured_content`, validated against a published `output_schema`.
- Product errors become readable tool errors (`not_found: pinneddemo 9.9 is not in the Pinned graph yet. known_versions: …`).
- Transports: stdio (`pinned mcp`) and Streamable HTTP at `/mcp` on the same FastAPI app (stateless + JSON responses,
  so any replica can answer any request). The SDK's `/mcp` route is added to the FastAPI router (not a catch-all
  mount), so every other path keeps FastAPI's JSON 404. Host allowlist from `PINNED_ALLOWED_HOSTS` (`*` = off, only
  behind a proxy that pins Host).

### 3. CLI — `src/pinned/cli.py` (argparse, no new dependency)
`pinned serve [--host --port --log-level]`, `pinned mcp`, `pinned check FILE|- --lib --version`,
`pinned how "task" --lib --version [--code FILE] [-k N]`, `pinned explain LIB PATH`, `pinned stats`, `pinned libraries`.
`--json` (before or after the command). `--api URL` / `PINNED_API` uses a running Pinned server instead of HydraDB
(for laptops without a graph). Exit codes: 0 ok · 1 `check` found problems (CI-friendly) · 2 input / not found ·
3 HydraDB or server unavailable. HydraDB down prints the URL, the `infra/hydradb-up.sh` command and the `--api` option.

### 4. Install docs for agents
Syntax checked against the current docs (code.claude.com/docs/en/mcp, Codex MCP docs, cursor.com/docs/context/mcp) on
15 Nov. The two Claude Code commands were also **run for real** with Claude Code 2.1.325 in this cloud session against
a live node: `claude mcp list` showed `pinned: … √ Connected` and `pinned-http: http://127.0.0.1:18800/mcp (HTTP) -
√ Connected` (entries removed afterwards). Codex / Cursor snippets are checked against docs only (no CLI here).

Claude Code, stdio (Pinned + HydraDB on your machine; `--env` must not sit right before the name, hence the order):
```bash
claude mcp add --env HYDRA_URL=http://127.0.0.1:8443 --transport stdio pinned -- uv --directory /path/to/pinned run pinned mcp
```
Claude Code, HTTP (a running `pinned serve`, local or hosted):
```bash
claude mcp add --transport http pinned https://<your-host>/mcp
```
Add `--scope user` to have it in every project, `--scope project` to share it via `.mcp.json`.

Codex (`~/.codex/config.toml`):
```toml
[mcp_servers.pinned]
command = "uv"
args = ["--directory", "/path/to/pinned", "run", "pinned", "mcp"]
env = { HYDRA_URL = "http://127.0.0.1:8443" }

# or the hosted endpoint:
# [mcp_servers.pinned]
# url = "https://<your-host>/mcp"
```
or `codex mcp add pinned --env HYDRA_URL=http://127.0.0.1:8443 -- uv --directory /path/to/pinned run pinned mcp` /
`codex mcp add pinned --url https://<your-host>/mcp`.

Cursor (`.cursor/mcp.json` in the project, or `~/.cursor/mcp.json`):
```json
{ "mcpServers": { "pinned": { "type": "stdio", "command": "uv",
  "args": ["--directory", "/path/to/pinned", "run", "pinned", "mcp"], "env": { "HYDRA_URL": "http://127.0.0.1:8443" } } } }
```
```json
{ "mcpServers": { "pinned": { "url": "https://<your-host>/mcp" } } }
```

### 5. Tests — `tests/server/` (59 tests, all against a real throwaway HydraDB node from the `graph` fixture)
- `test_server_http.py` (25): every endpoint happy path; validation (missing field, extra field, empty code, code over
  limit, bad version, bad library name, malformed JSON, k range, task length, bad path, passed type, error_type length);
  404 with hints for unknown library/version/API; case-insensitive library; per-version signature; outcome links the
  remembered `/v1/how` context and writes an Episode node; background maintain fires at the threshold; stats from a
  bench file + the real `evidence/load` file; curve 404 → 200 → 500 on broken JSON; JSON 404 for unknown routes;
  OpenAPI lists every endpoint; **HydraDB down**: every graph endpoint → 503 `graph_unavailable`, healthz → 503,
  validation still 422, curve still works.
- `test_server_limits.py` (10): 429 + Retry-After + headers, health never limited, per client behind a trusted proxy,
  X-Forwarded-For ignored when not trusted, limit off, bucket refill (fake clock), bounded memory, CORS allowed origin +
  preflight, foreign origin gets no CORS header, CORS off by default, preflight not rate-limited, settings from env.
- `test_server_mcp.py` (10): SDK in-memory `Client(server)`: tool list, descriptions, schemas, annotations,
  instructions; all four tools (text + structured); readable tool errors; HydraDB down → tool error; **stdio** through
  `python -m pinned.cli mcp`; **`/mcp` over real HTTP in-process** (uvicorn thread + SDK `Client(url)`) with REST on the
  same port; Host guard (421 for a foreign Host, 200 once allowed).
- `test_server_cli.py` (14): check (text, exit 1; json, exit 0), `--json` before the command, how (+ `--code`),
  explain, stats, input errors (exit 2), HydraDB down message (exit 3), default factory without parts B/C/D gives a
  clear message, factory spec errors, the `--api` remote backend against a running server (and an unreachable one),
  the installed `pinned` console script via subprocess, and `pinned serve` as a subprocess answering `/healthz`,
  `/v1/libraries` and `/mcp`.
- Test stand-ins for parts C and D live in `tests/server/server_fakes.py`: `GraphBackedSearch` and `RecordingLearner`
  are small but read/write the real HydraDB graph via `pinned.graph` helpers using the contract schema and validity
  rule (so every test does real graph round trips). Not product code.

### 6. Run it (real HydraDB node, separate server process)
`tests/server/live_run.py` starts nothing but the server: it seeds a small graph on a node you started, runs
`pinned serve` as a subprocess, calls every endpoint and every MCP tool over `/mcp`, plus `pinned mcp` over stdio.
```bash
infra/hydradb-up.sh pinned-live 18643 18687 18690
uv run python tests/server/live_run.py --hydra-url http://127.0.0.1:18643 --out live.md
```
Transcript of the run on 15 Nov (cloud VM, 4 vCPU) is at the end of this file. The latency numbers in it are single
calls on a 13-node demo graph, not a benchmark; the load numbers shown by `/v1/stats` are read from
`evidence/load/2026-11-15T104444+0000-synthetic.json` (part A's bench, Rohit's laptop).

## How to run
```bash
uv sync
infra/hydradb-up.sh pinned 8443 7687 9090          # a HydraDB node (HYDRA_URL defaults to http://127.0.0.1:8443)
uv run pinned serve --port 8000                     # REST at /v1/*, docs at /docs, MCP at /mcp
uv run pinned check my_script.py --lib pandas --version 2.0.3
uv run pinned how "append a row to a dataframe" --lib pandas --version 2.0.3
uv run pinned explain pandas pandas.DataFrame.append
uv run pinned stats --json
```
or `uvicorn --factory pinned.server.factory:app_from_env`. Env: `HYDRA_URL` (+ `HYDRA_TOKEN`, `HYDRA_GRAPH`, …
read by `HydraGraph`), `PINNED_CORS_ORIGINS`, `PINNED_ALLOWED_HOSTS` (set to the public host when deployed, else
`/mcp` answers 421), `PINNED_RATE_LIMIT`, `PINNED_TRUST_PROXY`, `PINNED_MAX_CODE_BYTES`, `PINNED_MAX_K`,
`PINNED_MAINTAIN_EVERY`, `PINNED_EVIDENCE_DIR`, `PINNED_INGEST_CACHE`, `PINNED_FACTORY`, `PINNED_API` (CLI).

## Test summary
`uv run pytest -q` → `74 passed in 17.84s` (15 graph + 59 server). `uv run pytest tests/server -q` → `59 passed`.

## Numbers and where they come from
- Test counts: the pytest runs above (this session).
- Live-run timings (e.g. `/v1/check` 14.1 ms, MCP `check_code` 52.0 ms round trip): the transcript below, one call
  each on a 13-node demo graph; NOT a latency claim. Real latency under load is part C's `evidence/search/` + part A's
  `evidence/load/`, which `/v1/stats` serves as-is.
- No other numbers are claimed.

## HydraDB findings
None new. Everything went through `pinned.graph` helpers and the documented query shapes. Design note only: a down
node costs ~3 s per request with `HydraGraph`'s default 4 retries (0.2+0.4+0.8+1.6 s back-off) before the 503;
`/healthz` uses the 5 s-timeout probe without retries, so it answers fast.

## Needs from main
- Nothing blocking. Phase 2: when B/C/D merge, `pinned serve` / `pinned mcp` work with no code change (default
  factory imports `pinned.search.GraphSearch`, `pinned.learn.GraphLearner`, `pinned.ingest.PyPIIngestor`). Until then
  the default factory fails with a clear "a Pinned part is not installed" message (tested).
- `pinned/__init__.py` has no `__version__`; the server reads it from package metadata (`importlib.metadata`). Fine
  as is; a `__version__` in the package root would be nicer (main's file).
- Deploy (Phase 4): set `PINNED_ALLOWED_HOSTS=<public host>` and `PINNED_TRUST_PROXY=1` behind the reverse proxy.

## Known limits (honest)
- Not run against the real search/learner yet (they are built in parallel); tested with graph-backed stand-ins.
  The server only relies on the contract protocols, so Phase 2 must re-run the live run with the real parts.
- A version must be in the graph exactly (by version_ord) or the API answers 404 with the nearest versions; it does not
  guess from the nearest ingested version.
- The rate limiter and the how→outcome context memory are per process (fine for one box; several replicas behind a
  balancer would each keep their own buckets and memory).
- `report_outcome` trusts the caller (no auth on the public API yet); a hostile client could send fake outcomes. The
  learner sees them as run_id `live`, separate from benchmark runs, so the eval curve is never affected. Phase 4 should
  decide on an API key or a per-client cap for `/v1/outcome`.
- `/mcp` is stateless (no server push / resumable streams); none of our tools need them.

---

## Live-run transcript (15 Nov 2026)

### HTTP + MCP (`tests/server/live_run.py`)
Live run 2026-11-15T11:16:55+00:00 · HydraDB `ghcr.io/hydra-db/hydradb:0.4.0` at http://127.0.0.1:18643 · seeded library `pinneddemo` (3 versions, 3 APIs, 4 terms) in 188.0 ms · `pinned serve` on http://127.0.0.1:36371 (separate process) · search/learn = test stand-ins over the real graph

#### health
`GET /healthz` → **200** · X-Response-Time-Ms 4.3
```
{
  "status": "ok",
  "hydradb": {
    "ok": true,
    "ms": 3.7
  },
  "version": "0.1.0"
}
```

#### libraries
`GET /v1/libraries` → **200** · X-Response-Time-Ms 5.1
```
{
  "libraries": [
    {
      "name": "pinneddemo",
      "import_names": [
        "pinneddemo"
      ],
      "versions": [
        {
          "version": "1.0.0",
          "ord": 1000000999,
          "released": "2020-01-01"
        },
        {
          "version": "1.5.0",
          "ord": 1005000999,
          "released": "2021-01-01"
        },
        {
          "version": "2.0.0",
          "ord": 2000000999,
          "released": "2022-01-01"
        }
      ]
    }
  ],
  "took_ms": 4.0
}
```

#### check (2.0 has removed old_sum)
`POST /v1/check {"code": "import pinneddemo\nvals = [1, 2, 3]\nprint(pinneddemo.old_sum(vals))\nprint(pinneddemo.mean(vals))\n", "library": "pinneddemo", "version": "2.0"}` → **200** · X-Response-Time-Ms 14.1
```
{
  "library": "pinneddemo",
  "version": "2.0.0",
  "ok": false,
  "findings": [
    {
      "call": "pinneddemo.old_sum",
      "status": "removed",
      "fix": "pinneddemo.total",
      "chain": [
        {
          "src": "pinneddemo.old_sum",
          "rel": "REMOVED_IN",
          "dst": "pinneddemo 2.0.0",
          "note": ""
        },
        {
          "src": "pinneddemo.old_sum",
          "rel": "REPLACED_BY",
          "dst": "pinneddemo.total",
          "note": ""
        }
      ],
      "line": 3
    },
    {
      "call": "pinneddemo.mean",
      "status": "valid",
      "fix": "",
      "chain": [],
      "line": 4
    }
  ],
  "took_ms": 13.0
}
```

#### check (same code is fine on 1.0.0)
`POST /v1/check {"code": "import pinneddemo\nvals = [1, 2, 3]\nprint(pinneddemo.old_sum(vals))\nprint(pinneddemo.mean(vals))\n", "library": "pinneddemo", "version": "1.0.0"}` → **200** · X-Response-Time-Ms 9.9
```
{
  "library": "pinneddemo",
  "version": "1.0.0",
  "ok": true,
  "findings": [
    {
      "call": "pinneddemo.old_sum",
      "status": "valid",
      "fix": "",
      "chain": [],
      "line": 3
    },
    {
      "call": "pinneddemo.mean",
      "status": "valid",
      "fix": "",
      "chain": [],
      "line": 4
    }
  ],
  "took_ms": 9.2
}
```

#### how
`POST /v1/how {"task": "sum the values", "library": "pinneddemo", "version": "2.0.0", "k": 5}` → **200** · X-Response-Time-Ms 18.1
```
{
  "library": "pinneddemo",
  "version": "2.0.0",
  "items": [
    {
      "library": "pinneddemo",
      "path": "pinneddemo.old_sum",
      "status": "removed",
      "chain": [
        {
          "src": "pinneddemo.old_sum",
          "rel": "REMOVED_IN",
          "dst": "pinneddemo 2.0.0",
          "note": ""
        },
        {
          "src": "pinneddemo.old_sum",
          "rel": "REPLACED_BY",
          "dst": "pinneddemo.total",
          "note": ""
        }
      ],
      "signature": "",
      "note": "use pinneddemo.total instead",
      "score": 1.0
    },
    {
      "library": "pinneddemo",
      "path": "pinneddemo.total",
      "status": "valid",
      "chain": [],
      "signature": "(x, *, skipna=True)",
      "note": "",
      "score": 1.0
    }
  ],
  "prompt": "Facts for pinneddemo==2.0.0 (from the Pinned graph):\n- pinneddemo.old_sum: removed. use pinneddemo.total instead [pinneddemo.old_sum -REMOVED_IN-> pinneddemo 2.0.0 ; pinneddemo.old_sum -REPLACED_BY-> pinneddemo.total]\n- pinneddemo.total: valid. signature: (x, *, skipna=True)",
  "query_ms": 16.99,
  "graph_queries": 8,
  "took_ms": 17.1
}
```

#### explain
`GET /v1/api/pinneddemo/pinneddemo.old_sum` → **200** · X-Response-Time-Ms 26.9
```
{
  "library": "pinneddemo",
  "path": "pinneddemo.old_sum",
  "kind": "function",
  "versions": [
    {
      "version": "1.0.0",
      "ord": 1000000999,
      "status": "valid",
      "signature": "(x)",
      "note": ""
    },
    {
      "version": "1.5.0",
      "ord": 1005000999,
      "status": "valid",
      "signature": "(x)",
      "note": ""
    },
    {
      "version": "2.0.0",
      "ord": 2000000999,
      "status": "removed",
      "signature": "",
      "note": ""
    }
  ],
  "replaced_by": [
    {
      "src": "pinneddemo.old_sum",
      "rel": "REPLACED_BY",
      "dst": "pinneddemo.total",
      "note": ""
    }
  ],
  "graph_queries": 13,
  "took_ms": 25.8
}
```

#### outcome (pass, same task as how)
`POST /v1/outcome {"code": "print(pinneddemo.total(vals))", "library": "pinneddemo", "version": "2.0.0", "passed": true, "task": "sum the values"}` → **200** · X-Response-Time-Ms 45.4
```
{
  "recorded": true,
  "episode_id": "live|live-a62b7b60aa423995|0",
  "used_context": true,
  "maintain_scheduled": false,
  "learned": {
    "edges_reweighted": 2,
    "shortcuts_added": 0,
    "gaps_ingested": 0,
    "merged": 0,
    "split": 0,
    "notes": []
  },
  "took_ms": 43.6
}
```

#### outcome (fail)
`POST /v1/outcome {"code": "import pinneddemo\nvals = [1, 2, 3]\nprint(pinneddemo.old_sum(vals))\nprint(pinneddemo.mean(vals))\n", "library": "pinneddemo", "version": "2.0.0", "p` → **200** · X-Response-Time-Ms 44.0
```
{
  "recorded": true,
  "episode_id": "live|live-45d1c89c8fbd9513|0",
  "used_context": true,
  "maintain_scheduled": false,
  "learned": {
    "edges_reweighted": 2,
    "shortcuts_added": 0,
    "gaps_ingested": 0,
    "merged": 0,
    "split": 0,
    "notes": []
  },
  "took_ms": 42.9
}
```

#### stats
`GET /v1/stats` → **200** · X-Response-Time-Ms 40.6
```
{
  "graph": {
    "nodes": {
      "Library": 1,
      "Version": 3,
      "Api": 3,
      "Term": 4,
      "Episode": 2
    },
    "edges": {
      "HAS_VERSION": 3,
      "NEXT": 2,
      "ADDED_IN": 3,
      "REMOVED_IN": 1,
      "CHANGED_IN": 1,
      "REPLACED_BY": 1,
      "ALIAS_OF": 0,
      "MEMBER_OF": 0,
      "CUES": 5,
      "SHORTCUT": 0,
      "USED": 4
    },
    "total_nodes": 13,
    "total_edges": 20,
    "count_ms": 38.1
  },
  "latency": {
    "graph_layer": {
      "file": "evidence/load/2026-11-15T104444+0000-synthetic.json",
      "utc": "2026-11-15T10:44:44+00:00",
      "latency": {
        "reads.validity_8_apis": [
          {
            "concurrency": 1,
            "calls": 400,
            "errors": 0,
            "qps": 127.8,
            "p50_ms": 7.18,
            "p95_ms": 11.64,
            "p99_ms": 12.94,
            "mean_ms": 7.81
          },
          {
            "concurrency": 8,
            "calls": 400,
            "errors": 0,
            "qps": 536.0,
            "p50_ms": 13.49,
            "p95_ms": 25.01,
            "p99_ms": 31.69,
            "mean_ms": 14.76
          },
          {
            "concurrency": 32,
            "calls": 400,
            "errors": 0,
            "qps": 339.4,
            "p50_ms": 94.33,
            "p95_ms": 122.04,
            "p99_ms": 136.98,
            "mean_ms": 92.05
          }
        ],
        "reads.replacement_chain_sspaths": [
          {
            "concurrency": 1,
            "calls": 400,
            "errors": 0,
            "qps": 331.5,
            "p50_ms": 2.85,
            "p95_ms": 4.53,
            "p99_ms": 5.56,
            "mean_ms": 3.01
          },
          {
            "concurrency": 8,
            "calls": 400,
            "errors": 0,
            "qps": 1084.7,
            "p50_ms": 6.67,
            "p95_ms": 11.85,
            "p99_ms": 14.35,
            "mean_ms": 7.25
          },
          {
            "concurrency": 32,
            "calls": 400,
            "errors": 0,
            "qps": 1096.8,
            "p50_ms": 26.53,
            "p95_ms": 52.51,
            "p99_ms": 73.68,
            "mean_ms": 27.71
          }
        ],
        "reads.cues_top20": [
          {
            "concurrency": 1,
            "calls": 400,
            "errors": 0,
            "qps": 265.0,
            "p50_ms": 3.61,
            "p95_ms": 6.03,
            "p99_ms": 7.0,
            "mean_ms": 3.76
          },
          {
           
... (1318 more characters)
```

#### curve (no curve.json yet)
`GET /v1/curve` → **404** · X-Response-Time-Ms 0.7
```
{
  "error": {
    "code": "not_found",
    "message": "no learning curve yet: evidence/curve.json is written by the evaluation run (Phase 2)",
    "details": {
      "expected": "evidence/curve.json"
    }
  }
}
```

#### unknown version (error shape)
`POST /v1/how {"task": "sum", "library": "pinneddemo", "version": "1.7"}` → **404** · X-Response-Time-Ms 3.9
```
{
  "error": {
    "code": "not_found",
    "message": "pinneddemo 1.7 is not in the Pinned graph yet",
    "details": {
      "known_versions": [
        "1.0.0",
        "1.5.0",
        "2.0.0"
      ],
      "nearest_lower": "1.5.0",
      "nearest_higher": "2.0.0"
    }
  }
}
```

#### bad input (error shape)
`POST /v1/how {"task": "sum", "library": "pinneddemo", "version": "2.0", "k": 0}` → **422** · X-Response-Time-Ms 0.5
```
{
  "error": {
    "code": "bad_input",
    "message": "k must be an integer from 1 to 32",
    "details": {
      "got": 0
    }
  }
}
```

#### MCP over HTTP: list_tools
`POST http://127.0.0.1:36371/mcp tools/list` → **ok**
```
[
  {
    "name": "check_code",
    "description": "Check every call to `library` in `code` against the exact installed `version`. Call this after you write or edit code that uses a version-pinned library, and before running it. Returns one line per call: its status at that version (valid / removed / renamed / changed / added_later / deprecated / missing), the fix to use instead, and the graph evidence chain (e.g. `pandas.DataFrame.append -REMOVED_IN-> pandas 2.0 ; ... -REPLACED_BY-> pandas.concat`). `ok` is true only when every call is valid."
  },
  {
    "name": "how_to",
    "description": "Get the API facts for doing `task` with `library` at the exact installed `version`: which functions/classes to use, their signature at that version, what to avoid (removed/renamed calls) and why, as evidence chains from the Pinned graph. Call this BEFORE writing code for a pinned version, especially for older or newer versions than you know well. Pass your current `code` if you have some. Returns up to `k` facts, one per line."
  },
  {
    "name": "explain_api",
    "description": "Show the history of one API (`path`, a dotted import path like `scipy.misc.comb` or `pandas.DataFrame.append`) across every ingested version of `library`: when it was added, changed signature, was deprecated or removed, and what replaced it. Call this when you are unsure whether a specific function/class/argument exists at your version or what to use instead."
  },
  {
    "name": "report_outcome",
    "description": "Report the outcome of running code for `library`==`version`: `passed` true/false, and on failure the `error_type` (AttributeError, ImportError, TypeError, ...) and `error_text` (the trimmed error message). Call this every time you run code you wrote with Pinned's help, pass or fail, and pass the same `task` you gave how_to. Pinned learns from it (strengthens facts that worked, weakens ones that failed, and fills gaps the error reveals), so later answers get better. Never include secrets in error_text."
  }
]
```

#### MCP over HTTP: check_code (text the agent reads)
`tools/call check_code {"code": "import pinneddemo\nprint(pinneddemo.old_sum([1, 2]))\n", "library": "pinneddemo", "version": "2.0.0"}` → **ok** · 52.0 ms round trip
```
pinneddemo==2.0.0: 1 of 1 calls need attention.
- line 2: pinneddemo.old_sum: removed -> use pinneddemo.total [pinneddemo.old_sum -REMOVED_IN-> pinneddemo 2.0.0 ; pinneddemo.old_sum -REPLACED_BY-> pinneddemo.total]
```

#### MCP over HTTP: check_code (structured content)
`tools/call check_code` → **ok**
```
{
  "library": "pinneddemo",
  "version": "2.0.0",
  "ok": false,
  "findings": [
    {
      "call": "pinneddemo.old_sum",
      "status": "removed",
      "fix": "pinneddemo.total",
      "chain": [
        {
          "src": "pinneddemo.old_sum",
          "rel": "REMOVED_IN",
          "dst": "pinneddemo 2.0.0",
          "note": ""
        },
        {
          "src": "pinneddemo.old_sum",
          "rel": "REPLACED_BY",
          "dst": "pinneddemo.total",
          "note": ""
        }
      ],
      "line": 2
    }
  ],
  "took_ms": 9.4
}
```

#### MCP over HTTP: how_to (text the agent reads)
`tools/call how_to {"task": "sum the values", "library": "pinneddemo", "version": "2.0.0", "k": 4}` → **ok** · 25.6 ms round trip
```
Facts for pinneddemo==2.0.0 (from the Pinned graph):
- pinneddemo.old_sum: removed. use pinneddemo.total instead [pinneddemo.old_sum -REMOVED_IN-> pinneddemo 2.0.0 ; pinneddemo.old_sum -REPLACED_BY-> pinneddemo.total]
- pinneddemo.total: valid. signature: (x, *, skipna=True)
```

#### MCP over HTTP: how_to (structured content)
`tools/call how_to` → **ok**
```
{
  "library": "pinneddemo",
  "version": "2.0.0",
  "items": [
    {
      "library": "pinneddemo",
      "path": "pinneddemo.old_sum",
      "status": "removed",
      "chain": [
        {
          "src": "pinneddemo.old_sum",
          "rel": "REMOVED_IN",
          "dst": "pinneddemo 2.0.0",
          "note": ""
        },
        {
          "src": "pinneddemo.old_sum",
          "rel": "REPLACED_BY",
          "dst": "pinneddemo.total",
          "note": ""
        }
      ],
      "signature": "",
      "note": "use pinneddemo.total instead",
      "score": 1.0
    },
    {
      "library": "pinneddemo",
      "path": "pinneddemo.total",
      "status": "valid",
      "chain": [],
      "signature": "(x, *, skipna=True)",
      "note": "",
      "score": 1.0
    }
  ],
  "prompt": "Facts for pinneddemo==2.0.0 (from the Pinned graph):\n- pinneddemo.old_sum: removed. use pinneddemo.total instead [pinneddemo.old_sum -REMOVED_IN-> pinneddemo 2.0.0 ; pinneddemo.old_sum -REPLACED_BY-> pinneddemo.total]\n- pinneddemo.total: valid. signature: (x, *, skipna=True)",
  "query_ms": 15.81,
  "graph_queries": 8,
  "took_ms": 16.3
}
```

#### MCP over HTTP: explain_api (text the agent reads)
`tools/call explain_api {"library": "pinneddemo", "path": "pinneddemo.total"}` → **ok** · 33.9 ms round trip
```
pinneddemo pinneddemo.total (function) across 3 versions:
- 1.0.0: added_later
- 1.5.0: valid (x)
- 2.0.0: valid (x, *, skipna=True)
```

#### MCP over HTTP: explain_api (structured content)
`tools/call explain_api` → **ok**
```
{
  "library": "pinneddemo",
  "path": "pinneddemo.total",
  "kind": "function",
  "versions": [
    {
      "version": "1.0.0",
      "ord": 1000000999,
      "status": "added_later",
      "signature": "",
      "note": ""
    },
    {
      "version": "1.5.0",
      "ord": 1005000999,
      "status": "valid",
      "signature": "(x)",
      "note": ""
    },
    {
      "version": "2.0.0",
      "ord": 2000000999,
      "status": "valid",
      "signature": "(x, *, skipna=True)",
      "note": ""
    }
  ],
  "replaced_by": [],
  "graph_queries": 12,
  "took_ms": 23.9
}
```

#### MCP over HTTP: report_outcome (text the agent reads)
`tools/call report_outcome {"code": "print(pinneddemo.total([1, 2]))", "library": "pinneddemo", "version": "2.0.0", "passed": true, "task": "sum the values"}` → **ok** · 49.3 ms round trip
```
Recorded pass for pinneddemo==2.0.0 as live|live-3094d16c2942f1e6|0. Learned: 2 edges reweighted, 0 shortcuts, 0 gaps ingested, 0 merged, 0 split.
```

#### MCP over HTTP: report_outcome (structured content)
`tools/call report_outcome` → **ok**
```
{
  "recorded": true,
  "episode_id": "live|live-3094d16c2942f1e6|0",
  "used_context": true,
  "maintain_scheduled": false,
  "learned": {
    "edges_reweighted": 2,
    "shortcuts_added": 0,
    "gaps_ingested": 0,
    "merged": 0,
    "split": 0,
    "notes": []
  },
  "took_ms": 41.9
}
```

#### MCP over stdio (`pinned mcp`): list_tools + check_code
`pinned mcp (stdio)` → **ok**
```
{
  "tools": [
    "check_code",
    "how_to",
    "explain_api",
    "report_outcome"
  ],
  "check_code text": "pinneddemo==2.0.0: 1 of 1 calls need attention.\n- line 2: pinneddemo.old_sum: removed -> use pinneddemo.total [pinneddemo.old_sum -REMOVED_IN-> pinneddemo 2.0.0 ; pinneddemo.old_sum -REPLACED_BY-> pinneddemo.total]"
}
```

### CLI session against the same node
```
$ pinned check job.py --lib pinneddemo --version 2.0
pinneddemo==2.0.0: 1 of 2 calls need attention.
- line 3: pinneddemo.old_sum: removed -> use pinneddemo.total [pinneddemo.old_sum -REMOVED_IN-> pinneddemo 2.0.0 ; pinneddemo.old_sum -REPLACED_BY-> pinneddemo.total]
- line 4: pinneddemo.mean: valid
[exit 1]
$ pinned how 'sum the values' --lib pinneddemo --version 2.0.0 -k 3
Facts for pinneddemo==2.0.0 (from the Pinned graph):
- pinneddemo.old_sum: removed. use pinneddemo.total instead [pinneddemo.old_sum -REMOVED_IN-> pinneddemo 2.0.0 ; pinneddemo.old_sum -REPLACED_BY-> pinneddemo.total]
- pinneddemo.total: valid. signature: (x, *, skipna=True)
[exit 0]
$ pinned explain pinneddemo pinneddemo.old_sum
pinneddemo pinneddemo.old_sum (function) across 3 versions:
- 1.0.0 .. 1.5.0: valid (x)
- 2.0.0: removed
replaced by: pinneddemo.old_sum -REPLACED_BY-> pinneddemo.total
[exit 0]
$ pinned libraries
pinneddemo: 1.0.0, 1.5.0, 2.0.0
[exit 0]
$ pinned how sum --lib pinneddemo --version 1.7
pinned: pinneddemo 1.7 is not in the Pinned graph yet
  known_versions: ['1.0.0', '1.5.0', '2.0.0']
  nearest_lower: 1.5.0
  nearest_higher: 2.0.0
[exit 2]
$ HYDRA_URL=http://127.0.0.1:1 pinned stats
pinned: HydraDB is not reachable at http://127.0.0.1:1 (ConnectError)
  HYDRA_URL=http://127.0.0.1:1
  Start a local node:  infra/hydradb-up.sh pinned 8443 7687 9090
  or point HYDRA_URL at a running HydraDB, or use a Pinned server:  pinned --api https://<host> ...
[exit 3]
$ pinned stats
graph: 14 nodes, 22 edges
  Library: 1
  Version: 3
  Api: 3
  Term: 4
  Episode: 3
  -HAS_VERSION->: 3
  -NEXT->: 2
  -ADDED_IN->: 3
  -REMOVED_IN->: 1
  -CHANGED_IN->: 1
  -REPLACED_BY->: 1
  -ALIAS_OF->: 0
  -MEMBER_OF->: 0
  -CUES->: 5
  -SHORTCUT->: 0
  -USED->: 6
latency (graph_layer) from evidence/load/2026-11-15T104444+0000-synthetic.json:
  reads.validity_8_apis: c=1 p50 7.18ms p95 11.64ms, c=8 p50 13.49ms p95 25.01ms, c=32 p50 94.33ms p95 122.04ms
  reads.replacement_chain_sspaths: c=1 p50 2.85ms p95 4.53ms, c=8 p50 6.67ms p95 11.85ms, c=32 p50 26.53ms p95 52.51ms
  reads.cues_top20: c=1 p50 3.61ms p95 6.03ms, c=8 p50 7.72ms p95 13.78ms, c=32 p50 46.58ms p95 57.95ms
  reads.mspaths_8x8: c=1 p50 6.14ms p95 8.49ms, c=8 p50 6.52ms p95 11.21ms, c=32 p50 26.5ms p95 47.33ms
```
