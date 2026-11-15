# PROGRESS — Pinned (main session writes this; cloud sessions write docs/progress/<session>.md)

Hackathon cutoff 9:00 AM IST Mon 16 Nov 2026. Phases: PLAN.md section 6.

## Phase 0 — DONE (main session, 15 Nov)
- Graph layer `src/pinned/graph/`: hardened client (all pages, typed/null cells, 422 guard, retries on idempotent calls,
  pooled + thread-safe, request counter), `ops.py` (batched idempotent nodes/edges incl. batched edge PROPERTIES,
  `paths`/`reach`/`multi_paths`, `edges_from`, counts, delete), `version_ord`.
- `tests/conftest.py`: `graph` fixture = throwaway HydraDB node on free ports (or `HYDRA_URL`), auto-removed.
  `uv run pytest -q` → 15 passed.
- Load bench `bench/load/run.py` → `evidence/load/*-synthetic.json` (22k nodes / 46k edges built in 17.5 s; reads p50
  2.9–7.2 ms at 1 client, ~1,100 qps at 8, 0 errors at 32).
- HydraDB pinned to `ghcr.io/hydra-db/hydradb:0.4.0`. Findings `docs/hydradb-findings.md`: 4 NEW (incl. the guide
  wrongly saying batched `UNWIND…MATCH…SET` is rejected — it works and is 55× faster than per-edge MERGE), 7 already
  filed upstream.
- Contract frozen for Phase 1 (keys, weights + floor, validity rule, signature storage, SHORTCUT range, exported class
  names, `explain_api` timeline). Phase 1 deps pre-added (griffe, fastapi, uvicorn, mcp, huggingface_hub, matplotlib).
- Repo: github.com/rohit-jsfreaky/pinned (private).

## Phase 1 — RUNNING (5 cloud sessions; prompts in prompts/phase1/)
| session | prompt | branch | status |
|---|---|---|---|
| B ingest | prompts/phase1/B-ingest.md | | |
| C search | prompts/phase1/C-search.md | | |
| D learn | prompts/phase1/D-learn.md | | |
| E eval harness | prompts/phase1/E-eval.md | | |
| F server + MCP | prompts/phase1/F-server.md | | |
Merge rule: main session reviews each branch, merges into `main`, runs the whole suite, then writes Phase 2 prompts.
