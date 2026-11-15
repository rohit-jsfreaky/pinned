# Pinned — HydraDB Hackathon, Track 1 (the self-improving graph)

**One line:** a HydraDB graph of every public API of 26 Python libraries across 282 releases (1,012,306 APIs) that
tells a coding agent whether its code is valid for the exact version you run, walks to the right call when it isn't,
and learns from every pass/fail the agent reports.

- Live app: `<live-url>` (try **Break it**: paste code, pick a version) · MCP endpoint: `<live-url>/mcp`
- Repo: this repository · Loom: `<loom-url>`

## What we built
| piece | what it does | proof |
|---|---|---|
| Ingest | PyPI wheels → static (griffe) + dynamic inspection → per-version diffs → `ADDED_IN / REMOVED_IN / CHANGED_IN / REPLACED_BY / ALIAS_OF / MEMBER_OF / CUES` | 282 versions, 1.01M APIs, 4.6M edges (`evidence/ingest/summary.json`) |
| Search | validity at a version; `algo.SSpaths` / `MSpaths` walks from an invalid call to the nearest valid one; the walk is the proof | `check_calls` p50 8.6 ms, 0 errors at 32 clients on the full graph (`evidence/search/latency-real.json`) |
| Learning | edge reweighting, learned cues, gap-driven ingestion, entity merge, entity split, path compression (shortcuts), decay | 17,253 logged moves in 2 rounds (`evidence/learn/pinned-curve-1.jsonl`) |
| Evaluation | GitChameleon 2.0 harness: 99 deduped venvs, runner self-check 328/328 with the official solutions, `claude -p` solver, cached completions, resumable curve driver | `evidence/harness/`, `evidence/runs/` |
| Product | MCP server (`check_code`, `how_to`, `explain_api`, `report_outcome`), HTTP API, CLI, website | `src/pinned/server/`, `web/` |

## Results (held-out 169 tasks; learning only on the 159 train tasks; same model and template for every arm)
| arm | pass@1 | wrong-version API errors |
|---|---|---|
| agent alone | 62.7% | 15 |
| agent + official docs | 67.5% | 13 |
| agent + Pinned, static graph (iteration 0) | 66.9% | 9 |
| after learning round 1 | 64.5% | 9 |
| after learning round 2 | 67.5% | 7 |
| control: random APIs valid at the version | 68.6% | 9 |
| ablation: same signals in a plain store | not run (export of the 1M-node graph hit HydraDB read limits; see finding 6) | — |

**Honest read.** Pinned cuts the errors it targets by 40% (15 → 9, 7 after learning). Overall pass@1 does not beat
plain docs, and the learning curve is flat within noise (23 of 169 tasks flip between identical one-sample runs). The
random-valid-API control matches it, so what helps today is the graph's version-valid API set, not the learned
ranking. We stopped here for the deadline. Next: repeated samples per task, a check-then-fix loop (the agent runs
`check_code` and fixes flagged calls before running), and learned cues scoped per library/version.

## HydraDB findings (v0.4.0, `ghcr.io/hydra-db/hydradb:0.4.0`) — exact queries in the git history of `docs/`
1. **The guide says batched `UNWIND … MATCH … SET` is rejected; it works for relationship properties** and is the only
   fast path: 2,466 edges/s vs 44.8/s for one `MERGE` per edge.
2. **Batch-CREATE idempotency keys clash after a node restarts from a saved store:** the key is built from a
   per-process query counter that restarts at 0 while old keys persist → `internal query execution error` on
   `UNWIND $rows AS row CREATE (a {id: row.src})-[:CUES]->(b {id: row.dst})`. Workaround: single-edge `MERGE`.
3. **`WHERE a.id IN $list` on an edge pattern is a full edge scan** (server logs `FullEdgeScan:CUES`), while the node
   form uses the id index. Workaround: map-anchored reads per id on the smaller side.
4. **The first `IN` condition picks the anchor**, not the cheapest one (16.9 ms vs 206.3 ms swapped).
5. **A label on an id-anchored read is ~100× slower** (10.9 s vs 0.09 s for 1,000 ids on 136k nodes).
6. **Admission limits at ~1M nodes:** result rows > 100,000, edge rows > 250,000 and label-index candidates > 250,000
   are rejected even when paged, and per-library scans hit the fixed 30 s timeout. Workaround: compute ids locally
   (`node_id`) and read by id in chunks.
7. **`UNWIND` cannot drive a read** (`UNWIND $ids AS i MATCH (a {id: i}) …` is rejected) — batched point reads need
   one request per anchor.
8. Smaller: `sum()` rejects float properties; bound variable-length relationships (`-[r:R*1..2]->`) fail;
   `count(n)` gives an unrelated error; `CONTAINS` unsupported (#130); `SSpaths pathCount: 0` stops early (#188);
   node batches need every field on every row; ~22 ms per single write that does not scale with clients.

## Who pays
Teams whose agents write code against pinned dependencies (most production Python). Free: local MCP + the public
graph. Paid: a hosted shared graph that learns from a whole org's agent runs, private-package ingestion, and
CI checks that block merges calling APIs your lockfile doesn't have.
