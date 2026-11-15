# Phase 2 · B — controls (ablation, negative control, real-graph latency)

Session B, 15 Nov 2026, 19:02–19:30 UTC (35-minute cap). Branch `claude/modest-feynman-d4qz2c`.

## Setup
- Full graph loaded from branch `graph-snapshot` (sha256 OK) into node `pinned-g` on 18443. `explain pandas
  pandas.DataFrame.append` shows 1.3.5 valid -> 1.4.0 deprecated -> 2 removed, chain `-REPLACED_BY-> pandas.concat`.
- Dataset 799a6a33, split evidence/split.json (169 TEST). Venvs 99/99 built here (Python 3.7 built from source).
- Solver `claude -p`, alias `haiku` -> `claude-haiku-5-5`, same template, concurrency 8.
- Docker on this VM had a stale `/var/run/docker.pid` from the image; removed it, `infra/ensure-docker.sh` then works.

## Results (all from real runs under evidence/)
| arm | run | iteration | TEST pass@1 |
|---|---|---|---|
| none (baseline, P1) | baselines-p1 | 0 | 0.627 (106/169) |
| docs (baseline, P1) | baselines-p1 | 0 | 0.675 (114/169) |
| **shuffled** (negative control) | shuffled-curve-1 | 0 | **0.686 (116/169)**, 0 missing |
| plain (ablation) | plain-curve-1 | — | **NOT RUN** (see limits) |

Shuffled = `ShuffledSearch(GraphSearch(g), seed=13)`, mode `items`: same number of items, random valid paths for the
library/version, scores carried top-down. It is iteration 0 only; nothing was learned or written to the graph.
Compare it to the graph arm's iteration 0 from session A (same solver/template/split). Do not read 0.686 as "graph
ranking helps" or "doesn't" until A's number is next to it.

## Real-graph latency — evidence/search/latency-real.json
`bench.load.real` (new, `bench/load/real.py`): first 100 TEST problems' (problem, starting_code, library, version);
`check_calls` gets the STARTING code (no solutions). Client + node on the same 4-vCPU VM; the shuffled run's `claude -p`
solves were running at the same time. 0 errors in all measured runs.

| call | conc | qps | p50 ms | p95 ms | p99 ms |
|---|---|---|---|---|---|
| context_for (warm) | 1 | 5.3 | 172 | 381 | 395 |
| context_for (warm) | 8 | 18.3 | 404 | 705 | 793 |
| context_for (warm) | 32 | 18.6 | 1337 | 2766 | 3069 |
| check_calls | 1 | 85.0 | 8.6 | 24.4 | 39.5 |
| check_calls | 8 | 237.3 | 26.4 | 62.7 | 71.0 |
| check_calls | 32 | 180.7 | 133.3 | 263.7 | 477.7 |

Cold first pass (sequential): context_for p50 1047 ms, p95 3694 ms, max 15.6 s; check_calls p50 8 ms.
Graph round trips per call: context_for mean 6.2 (p50 5, max 27), check_calls mean 1.4. Counter mismatches 0.
Mean items returned 7.2 of k=8.
A first latency attempt (while the plain export ran 8 parallel heavy reads) failed: a search query hit HydraDB's 30 s
query timeout. The saved run is the second attempt, with the node otherwise idle.

## Code changes
- `src/pinned/eval/__main__.py`: `curve --arm-kind graph|plain|shuffled` (default `graph` = unchanged). New
  `curve_parts()` builds (search, learner): plain = one `PlainStore.from_graph(g, <every dataset library>)` used as
  BOTH search and learner; shuffled = `ShuffledSearch(GraphSearch(g), seed=13)` + `GraphLearner`. The arm kind is
  written into the run's config.json (`arm_kind`).
- `tests/eval/test_eval_arm_kinds.py`: 4 tests (each kind's wiring, split guard on, unknown kind rejected).
- `src/pinned/learn/plain.py` (part D's folder, needed to run at all): `_export` now reads ANCHORED (edges into a
  Version id; CUES/SHORTCUT into chunks of 1000 Api ids, 8 threads). The old unanchored read is rejected on the full
  graph. tests/learn/test_plain_shuffled.py: 11/11 pass on a throwaway node.
- `bench/load/real.py`: the real-graph latency bench above.

## Limits / HydraDB findings (for docs/hydradb-findings.md)
1. Plain ablation was NOT run. `PlainStore.from_graph` on the full graph:
   - old export: `MATCH (a:Api)-[r:ADDED_IN]->(v) RETURN ...` -> `resource_exhausted: cypher_vertex_label_index_candidates
     rejected by admission control: actual 250001 exceeds limit 250000`; with `WHERE a.library=$lib` -> 30 s
     `query_timeout` (library-filtered edge read is a full edge scan).
   - anchored export: fast for small versions (0.01–0.8 s), but one big version is slow to page out
     (pandas 1.3.5: 24,654 ADDED_IN rows in 17 s idle) and under load a version read hit
     `client_query_runtime exceeded query timeout after 30000 ms`. pandas alone exported in 65 s; the whole graph
     (~1M Api, plus CUES per Api chunk ~2 s each) does not fit the window.
   - Proper fix (not done): export per version in Api-id pages (read Api ids of a version via `HAS_VERSION`/ADDED_IN
     in pages, then out-edges by `a.id IN $ids`, which is the fast indexed shape), or build PlainStore at ingest time
     from the ingest snapshot instead of reading it back out of HydraDB. Then re-run:
     `curve --arm-kind plain --arm-name plain --run-id plain-curve-1 --iterations 1 --concurrency 8`.
2. HydraDB 0.4.0 query timeout is a fixed 30 s; heavy reads from one client can push the product's own search queries
   past it (seen once, see latency note).
3. `pytest tests/learn` with `HYDRA_URL` exported wrote test rows (unique test keys) into the real `pinned-g` node
   before I noticed; one fixture write errored. The snapshot on `graph-snapshot` is untouched; do not save this node.

## Needs from main
- Decide whether D (or a next B round) does the paged PlainStore export so the plain ablation can run.
- `evidence/curve.json` was rebuilt locally by `cmd_curve` and then reverted (session A owns it).
