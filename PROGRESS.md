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

## Phase 1 — 4 of 5 merged (15 Nov night)
| session | branch | status |
|---|---|---|
| B ingest | claude/adoring-noether-1irw8x | still running (full real ingest of all 81 pairs); not pushed yet |
| C search | claude/brave-cannon-wejqmm | merged |
| D learn | claude/elegant-sagan-1ws0cr | merged |
| E eval harness | claude/optimistic-brahmagupta-whpupo | merged — self-check 328/328; baselines on TEST: none 0.627, docs 0.675 |
| F server + MCP | claude/relaxed-keller-38xtcp | merged |
After the C/D/E/F merge: `uv run pytest -q` → 196 passed, 2 skipped.

## Website (Rohit asked to start it while B finishes)
- `web/` (Next.js 16, Tailwind 4, motion, Phosphor, Hanken Grotesk + JetBrains Mono). Landing: hero, product window,
  problem, the chain, bento, agents (MCP install), proof, FAQ, CTA, footer; `/try` "Break it" playground on the real API.
- Sections ported from real sites' extracted code (cal.com, dub.co, mintlify, attio). Research (14 sites' HTML/CSS)
  lives in `design/` locally only (git-ignored).
- Numbers come only from `evidence/` (`web/scripts/sync-evidence.mjs`, runs before dev/build).
- Verified: `/try` against the real GraphSearch + GraphLearner on a seeded node (check + how both answered, 8–10 ms).
- Run: `cd web && npm run build && PORT=3100 npm start` (API at PINNED_API_ORIGIN, default http://127.0.0.1:8000).
