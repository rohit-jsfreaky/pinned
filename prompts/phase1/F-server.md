# Phase 1 · Session F — product server: HTTP API, MCP server, CLI

You are session F of Phase 1 of **Pinned** (HydraDB hackathon, Track 1). Pinned tells coding agents whether code is
valid for the library version they actually have and walks a HydraDB graph to the right call for that version. The
product has NO model inside: the user's own agent (Claude Code, Codex, Cursor) calls Pinned over MCP. Every outcome an
agent reports goes back into the graph, so Pinned gets sharper with use. You build the server, the MCP server and the
CLI. (The website comes in Phase 3, after Rohit picks the design — do NOT build any web UI.)

Your folders: `src/pinned/server/`, `src/pinned/cli.py` (the `pinned` script entry point is already
`pinned.cli:main`), `tests/server/`. Progress file: `docs/progress/F-server.md`.

## Start
1. Read, in this order: `CLAUDE.md`, `PLAN.md`, `src/pinned/contract.py` (frozen; the schema, weights, validity rule and
   the class names every part exports), `docs/facts/hydradb.md`, `docs/hydradb-findings.md`, `docs/facts/gitchameleon.md`,
   and the graph layer you must use: `src/pinned/graph/` (+ `tests/graph/test_ops.py` shows every helper in use).
2. `uv sync`. Start Docker if it is not running (cloud VMs do not start it): `infra/ensure-docker.sh`. Make sure the
   HydraDB image is local: `docker pull ghcr.io/hydra-db/hydradb:0.4.0`.
3. Run `uv run pytest tests/graph -q` once: it starts a throwaway HydraDB node (fixture `graph` in `tests/conftest.py`)
   and must pass. Your tests use the same `graph` + `run_key` fixtures. For manual runs start a node with
   `infra/hydradb-up.sh <name> <http> <bolt> <admin>` and set `HYDRA_URL`.

## Rules (all Phase 1 sessions)
- Touch ONLY your folders (listed below). Do NOT edit `src/pinned/contract.py`, `pyproject.toml`, `uv.lock`, `PLAN.md`,
  `PROGRESS.md`, `CLAUDE.md`, `src/pinned/graph/`, `tests/conftest.py`, `docs/facts/`, `docs/hydradb-findings.md` or
  another part's folder. Every Phase 1 dependency is already installed. If you truly need a new dependency or a
  contract change, do not hack around it: write it under "Needs from main" in your progress file and carry on with the
  rest.
- Every graph read/write goes through `pinned.graph` helpers or `HydraGraph.query/write` (batched, idempotent forms
  only; never plain single `CREATE`). HydraDB is the only graph store.
- A HydraDB limit you hit: work around it properly and record it in your progress file under "HydraDB findings"
  (exact query, exact error, workaround). The main session moves them into the findings doc.
- No fabricated numbers. Every number you report comes from a run you saved under your evidence folder.
- Never read or use the GitChameleon answer key (`solution`, `ground_truth_solutions.jsonl`, `hidden_tests/`) —
  the only exception is session E's runner self-check.
- Product-grade code: typed, small modules, real error handling, no stubs/TODOs/temporary hacks in product code.
  Fakes and fixtures live only under `tests/`.
- Do the WHOLE scope below. Never cut scope. Done = every item below is built, tested, and run where it says "run".
- Talk in simple English in your progress file.

## Finish
1. `uv run pytest -q` (whole suite) passes.
2. Write `docs/progress/F-server.md`: what is done (per numbered item), how to run it, test summary, every number with
   its evidence path, HydraDB findings, needs from main, known limits (honest).
3. Commit in clear steps and push your branch (never push to `main`). Your last message: the branch name and a
   one-paragraph summary.

Sessions B (ingest), C (search) and D (learn) are built in parallel. Code against the protocols in contract.py and the
exact class names written there: `pinned.search.GraphSearch(graph)`, `pinned.learn.GraphLearner(graph, ingestor=...,
run_id="live")`, `pinned.ingest.PyPIIngestor(graph, cache_dir=...)`. Build the app with dependency injection
(`create_app(search, learner, graph)`), and one factory that builds the real objects from env (`HYDRA_URL` etc.) by
importing those names. Tests use fakes under `tests/server/` only. In Phase 2 the real objects plug in with no code
change.

## Scope
1. **HTTP API** (FastAPI): `POST /v1/check {code, library, version}` → findings; `POST /v1/how {task, code?, library,
   version, k?}` → context (items + chains); `GET /v1/api/{library}/{path}` → API timeline; `POST /v1/outcome {code,
   library, version, passed, error_type?, error_text?, task?}` → records the outcome through the learner (run_id
   "live"); `GET /v1/libraries` → libraries + versions in the graph; `GET /v1/stats` → graph size (count helpers, per
   label/type) + latest latency numbers from `evidence/load/` and `evidence/search/`; `GET /v1/curve` → `evidence/curve.json`
   (clear 404 when absent); `GET /healthz` (checks HydraDB too). Pydantic models, input limits (code size, k range),
   clear errors, CORS origins from env, a simple per-client rate limit, request timing in responses.
2. **MCP server** (official MCP Python SDK; read its CURRENT docs first): tools `check_code`, `how_to`, `explain_api`,
   `report_outcome`, with descriptions written for coding agents (when to call each, what comes back). Transports:
   stdio (`pinned mcp`) and Streamable HTTP mounted on the same app at `/mcp`. Tool results = compact text an agent can
   use directly (the evidence chain in one line per item) + structured content.
3. **CLI** (`pinned`): `serve` (uvicorn), `mcp` (stdio), `check FILE --lib --version`, `how "task" --lib --version`,
   `explain LIB PATH`, `stats`; `--json` output option; helpful errors when HydraDB is down.
4. **Install docs for agents** (in your progress file now; README later): exact `claude mcp add` commands for stdio and
   for the HTTP endpoint, plus Codex/Cursor config snippets — verify the Claude Code command syntax against its current
   docs.
5. **Tests**: every endpoint (happy path + validation errors + HydraDB down), rate limit, CORS, MCP tools through the
   SDK's in-memory client/session, CLI commands via subprocess or Click/Typer runner (whichever you use — no new
   dependency: argparse is fine), `/mcp` reachable over HTTP in-process.
6. **Run it**: start the server against a real HydraDB node with a small graph you seed in a test helper, call every
   endpoint and every MCP tool for real, save the transcript to `docs/progress/F-server.md`.
