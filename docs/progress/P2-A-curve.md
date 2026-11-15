# Phase 2 · A — the Pinned learning curve on the real graph

Branch: `claude/eager-clarke-mvi3xl`. Run: `evidence/runs/pinned-curve-1/`. Move log: `evidence/learn/pinned-curve-1.jsonl`.
Graph: the saved full graph from `graph-snapshot` (sha256 `aced2bde…84f5`, checked), loaded into HydraDB 0.4.0 node
`pinned-g`. `explain pandas pandas.DataFrame.append` shows the full ladder and the `-REPLACED_BY-> pandas.concat` chain.

## Status (honest)
- **Iteration 0 (static graph) on TEST: done.** 113/169 = **0.669**.
- **Iteration 1 learning on TRAIN: done** (159 train episodes recorded, `maintain()` once).
- **Iteration 1 measure on TEST: running at the 35-minute cap.** It was NOT stopped (stopping it would leave no
  post-learning point at all). This file and `evidence/curve.json` are updated when it lands.
- **Iteration 2: not run yet** (same command resumes it; every finished stage is kept).

## Curve so far (TEST, n=169, one sample per problem, `claude -p --model haiku`)
| arm | pass@1 | passed | source |
|---|---|---|---|
| none (agent alone) | 0.627 | 106 | `evidence/runs/baselines-p1` |
| docs (plain docs) | 0.675 | 114 | `evidence/runs/baselines-p1` |
| **Pinned, iteration 0 (static graph)** | **0.669** | 113 | `evidence/runs/pinned-curve-1` |
| Pinned, iteration 1 | pending | | |
| Pinned, iteration 2 | not run | | |

Train split, iteration 1 (graph before learning, the episodes the learner saw): 102/159 = 0.642.

Static graph vs none: +7 problems (+4.2 pts). vs docs: −1 problem (within single-sample noise; see E's notes).

### Per library, TEST, iteration 0 (passed out of n)
| library | n | none | docs | pinned it0 |
|---|---|---|---|---|
| django | 5 | 3 | 4 | 4 |
| falcon | 11 | 7 | 7 | 7 |
| flask | 11 | 9 | 8 | 9 |
| geopandas | 4 | 2 | 1 | 2 |
| gradio | 4 | 1 | 2 | 3 |
| jinja2 | 2 | 1 | 2 | 1 |
| kymatio | 1 | 0 | 0 | 0 |
| librosa | 21 | 10 | 9 | 11 |
| lightgbm | 5 | 5 | 5 | 5 |
| matplotlib | 3 | 3 | 3 | 3 |
| mitmproxy | 5 | 2 | 2 | 2 |
| networkx | 5 | 3 | 4 | 3 |
| nltk | 3 | 3 | 2 | 3 |
| numpy | 8 | 8 | 8 | 8 |
| pandas | 5 | 1 | 2 | 1 |
| pillow | 3 | 0 | 1 | 1 |
| plotly | 5 | 5 | 5 | 5 |
| pytest | 5 | 4 | 5 | 5 |
| scikit-learn | 6 | 4 | 5 | 5 |
| scipy | 20 | 14 | 14 | 13 |
| seaborn | 4 | 4 | 4 | 4 |
| spacy | 2 | 2 | 2 | 2 |
| sympy | 17 | 10 | 11 | 11 |
| torch | 9 | 2 | 4 | 2 |
| tornado | 4 | 3 | 4 | 3 |
| tqdm | 1 | 0 | 0 | 0 |

## What learning changed (iteration 1; counts from `evidence/learn/pinned-curve-1.jsonl` and `learn.jsonl`)
| move | count | note |
|---|---|---|
| episode (provenance) | 159 | one per train problem |
| reweight | 422 | CUES/SHORTCUT/REPLACED_BY edges behind context items the code used |
| learn_cue | 1,831 | new/raised CUES from problem words to APIs in passing code |
| shortcut | 3 | flask: `config`/`file`/`json` -SHORTCUT-> `flask.Config.from_file` (chain `Config.from_json > from_file` passed 2x) |
| merge (ALIAS_OF) | 12,196 | nltk 9,641 · networkx 1,209 · pandas 568 · scipy 411 · spacy 223 · librosa 90 · falcon 48 · 6 others |
| merge_skipped | 6 | django, matplotlib, numpy, plotly, torch (30 s timeout), sympy (250k admission limit) |
| decay_scan_skipped | 2 | CUES (timeout), REPLACED_BY (admission); touched edges still checked |
| gap_fill / split / decay | 0 | no fill_gap, CHANGED_IN or decay in this iteration |

`maintain()` LearnReport (iteration 1): `merged=12196 shortcuts=0 split=0 decayed=0`. The 3 shortcuts were written by
the first `maintain()` attempt, which crashed afterwards in merge (fix 4); the resumed call found them already there.
`maintain()` took ~7 min on the full graph (mostly the per-library merge reads and the timeouts).

## Fixes made (all small, tested, committed)
1. `eval/pipeline.py` — `solve_stage` built arm contexts one by one (~1.4 s per problem on the full graph; ~4 min per
   stage before any solver call). Now a thread pool, order unchanged. Test: `test_solve_stage_builds_contexts_in_parallel_and_keeps_order`.
2. `eval/pipeline.py` — capped that pool at `CONTEXT_WORKERS = 4`: with 20 parallel searches a CUES read hit HydraDB's
   30 s query timeout. 159 contexts now take ~80 s.
3. `graph/ops.py` — `link_many` falls back to one `MERGE` per edge when a batch `CREATE` fails with `internal`. Cause
   (HydraDB finding below): idempotency-key clash after loading a saved store. Tests: `tests/graph/test_link_fallback.py`.
4. `learn/maintain.py` — at ~1M Api, `merge()` (no library filter for >1 library) and the first `decay()` scan are
   refused by HydraDB. Merge now reads one library at a time and skips + logs (`merge_skipped`) a library HydraDB
   refuses; decay falls back to the edges `record()` touched (`decay_scan_skipped`). `tests/learn`: 50 passed.
Also ran with `--concurrency 20` instead of 10 (solver throughput; recorded in `config.json`); no rate limit was hit.

## HydraDB findings (for `docs/hydradb-findings.md`)
1. **Batch-CREATE idempotency keys clash after a node restarts from a saved store.** Server log:
   `idempotency key conflict for create request key http-query-2031.unwind-create.00000000000000000000: this key already
   stored a result for a different edge` → client gets `internal: internal query execution error` for
   `UNWIND $rows AS row CREATE (a {id: row.src})-[:CUES]->(b {id: row.dst})`. The key is built from a per-process query
   counter that restarts at 0, but the keys stored before the save are kept. 68 clashes in the first minute of
   learning. `UNWIND … MERGE` of edges is not supported (`invalid_request: UNWIND vertex upsert requires MERGE by id
   followed by SET`), so the workaround is single-edge `MERGE`. Worth filing upstream.
2. **Per-library scans time out at full size.** `MATCH (a:Api)-[r:ADDED_IN]->(v) WHERE a.library = $lib RETURN …`:
   django/matplotlib/numpy/plotly/torch → `query_timeout` (30 s; `query_out_neighbors_scan`, `cypher_vertex_write_back`,
   `cypher_match_join`); sympy → `cypher_vertex_property_index_candidates … 250001 exceeds limit 250000`.
3. Unfiltered `MATCH (a:Api)-[:ADDED_IN]->(v)` → `cypher_vertex_label_index_candidates … 250001` (same as B's #3).

## Limits
- One sample per problem; 1-problem gaps are noise.
- Entity merge did not run for 6 big libraries (above). Decay checks only edges touched in the current process: the
  first ~100 iteration-1 records were made by a process that then crashed, so their edges are not in decay's set
  (decay needs ≥ 3 failures on an edge, so this matters little after one iteration).
- The learned graph is not saved (step 4 skipped, as the prompt says).

## How to resume
```bash
infra/hydradb-snapshot.sh load data/pinned-graph.tgz pinned-g 18443 17687 19090   # or the running node
HYDRA_URL=http://127.0.0.1:18443 uv run python -m pinned.eval curve --run-id pinned-curve-1 --iterations 2 --concurrency 20 --workers 4
uv run python -m pinned.eval curve-json
```
Note: the learned graph lives only in the running node `pinned-g` of this session. Resuming on a fresh load would
re-do iteration 1 learning on top of a static graph only if `learn.jsonl` is removed; as is, the driver would skip it
(it is logged as done) and measure on a graph without iteration 1's learning. Resume only on this node.
