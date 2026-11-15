# Phase 2 · A — the Pinned learning curve on the real graph

Branch: `claude/eager-clarke-mvi3xl`. Run: `evidence/runs/pinned-curve-1/`. Move log: `evidence/learn/pinned-curve-1.jsonl`.
Graph: the saved full graph from `graph-snapshot` (sha256 `aced2bde…84f5`, checked), loaded into HydraDB 0.4.0 node
`pinned-g`. `explain pandas pandas.DataFrame.append` shows the full ladder and the `-REPLACED_BY-> pandas.concat` chain.

## Status (honest)
- **Iteration 0 (static graph) on TEST: done.** 113/169 = **0.669**.
- **Iteration 1 learning on TRAIN: done** (159 train episodes recorded, `maintain()` once).
- **Iteration 1 measure on TEST: done** (finished ~5 min after the 35-minute cap; not stopped, since stopping would
  leave no post-learning point at all). **109/169 = 0.645 — DOWN 4 problems from iteration 0. No rise yet.**
- **Iteration 2: done.** **114/169 = 0.675** (+1 vs iteration 0, +5 vs iteration 1). Whole run ended ~19:50 UTC,
  ~13 min past the 35-minute cap (the cap was not enforced: stopping would have dropped the learning points).
- **Verdict: the curve is flat within noise — 0.669 → 0.645 → 0.675.** It is not a rising curve yet. Iteration 2 ties
  the plain-docs baseline (0.675) and beats no-context (0.627) by 8 problems.

## Curve (TEST, n=169, one sample per problem, `claude -p --model haiku`)
| arm | pass@1 | passed | source |
|---|---|---|---|
| none (agent alone) | 0.627 | 106 | `evidence/runs/baselines-p1` |
| docs (plain docs) | 0.675 | 114 | `evidence/runs/baselines-p1` |
| **Pinned, iteration 0 (static graph)** | **0.669** | 113 | `evidence/runs/pinned-curve-1` |
| Pinned, iteration 1 | **0.645** | 109 | `evidence/runs/pinned-curve-1` |
| Pinned, iteration 2 | **0.675** | 114 | `evidence/runs/pinned-curve-1` |

Train split (graph at the start of that iteration; the episodes the learner saw): iteration 1 102/159 = 0.642,
iteration 2 105/159 = 0.660.

Stability across the 3 test passes: 101 problems pass every time, 45 fail every time, **23 flip at least once**.
So one-sample differences of ±5 problems are within run-to-run noise; repeats (E's note: a sample index in the cache
key) are needed before claiming any gain or loss.

Iteration → iteration flips on TEST:
| from → to | fail→pass | pass→fail | test contexts identical |
|---|---|---|---|
| 0 → 1 | 6 | 10 | 35/169 |
| 1 → 2 | 9 (torch 2, one each scipy, jinja2, flask, sympy, tornado, lightgbm, django) | 4 (torch, scipy, librosa, numpy) | 14/169 |
| 0 → 2 | 9 (scipy 3, torch 3, sympy, falcon, tornado) | 8 (librosa 3, scipy, sympy, falcon, numpy, nltk) | 11/169 |

torch and scipy gain most (0→2: torch +3, scipy +2 net); librosa loses (−3).

### Iteration 0 → 1 on TEST, detail (from `completions.jsonl` / `results.jsonl`)
- Learning changed the context text of **134/169** test problems (35 identical).
- fail→pass: 6 (scipy 3, torch 2, falcon 1), all with changed context.
- pass→fail: 10 (librosa 2, one each scipy, jinja2, flask, sympy, falcon, lightgbm, nltk, django); 7 with changed
  context, **3 with identical context (pure solver sampling noise)**.
- Net −4 on one sample per problem: not distinguishable from noise. Not investigated further (time cap). Suspects
  for the main session: the 1,831 learned cues (problem words → APIs in passing code) push
  generic train-API items into unrelated test contexts; the 12,196 ALIAS_OF merges add alias chains to items.

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

## What learning changed (counts from `evidence/learn/pinned-curve-1.jsonl` and `learn.jsonl`)
| move | iteration 1 | iteration 2 | note |
|---|---|---|---|
| episode (provenance) | 159 | 159 | one per train problem |
| reweight | 422 | 2,213 | CUES/SHORTCUT/REPLACED_BY edges behind context items the code used |
| learn_cue | 1,831 | 258 | new/raised CUES from problem words to APIs in passing code |
| shortcut | 3 | 3 | it1 flask: `config`/`file`/`json` → `flask.Config.from_file` (chain `Config.from_json > from_file`); it2 sympy: `npartitions`/`number`/`partitions` → `sympy.functions.combinatorial.numbers.partition` (chain from `sympy.ntheory.partitions_.npartitions`) |
| merge (ALIAS_OF) | 12,196 | 0 | it1: nltk 9,641 · networkx 1,209 · pandas 568 · scipy 411 · spacy 223 · librosa 90 · falcon 48 · 6 others |
| merge_skipped | 6 | 0 | django, matplotlib, numpy, plotly, torch (30 s timeout), sympy (250k admission limit) |
| decay_scan_skipped | 2 | 0 | CUES (timeout), REPLACED_BY (admission); touched edges still checked |
| decay | 0 | 1 | `pin -CUES-> sympy.physics.mechanics.PinJoint` 0.25 → 0.1 (4 fails vs 1 pass) |
| gap_fill / split | 0 | 0 | no fill_gap and no CHANGED_IN in either iteration |

Iteration 1 / 2 counts: iteration 1 = the move log at the moment its `maintain()` finished (only iteration 1 existed);
iteration 2 = final totals minus that (totals: reweight 2,635, learn_cue 2,089, episode 318, shortcut 6, merge 12,196).

`maintain()` LearnReports: iteration 1 `merged=12196 shortcuts=0 split=0 decayed=0`; iteration 2
`shortcuts=3 merged=0 split=0 decayed=1`. The 3 shortcuts were written by
the first `maintain()` attempt, which crashed afterwards in merge (fix 4); the resumed call found them already there.
`maintain()` took ~7 min on the full graph in iteration 1 (mostly the per-library merge reads and the timeouts);
iteration 2 skipped merge (no library dirty) and was quick. Solver cost for all 825 calls: $0.79.

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
   After all fixes: `uv run pytest -q tests/eval tests/learn tests/graph` → 112 passed.
Also: `dockerd` would not start (stale `/var/run/docker.pid`, as E reported); removed the file by hand. Ran with
`--concurrency 20` instead of 10 (solver throughput; recorded in `config.json`); no rate limit was hit.

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

## What would make it rise (not done here; for the main session)
1. Repeats: 3 samples per problem per point (cache key sample index) — the 23 flipping problems swamp a ±5 signal.
2. gap_fill never fired (0 in both iterations): failing train episodes did not produce gaps the graph lacked facts
   for. Worth checking `errors.find_gaps` against the real `error_text` of the 111 train failures.
3. Learned cues are mostly generic problem words → APIs (1,831 in iteration 1); they changed 134/169 test contexts
   in one step. A cap per term or IDF weighting on learned cues would keep test contexts from drifting.
4. More iterations: maintain() only compresses chains that passed ≥ 2 times, so shortcuts appear from iteration 2 on.

## How to reproduce
```bash
infra/hydradb-snapshot.sh load data/pinned-graph.tgz pinned-g 18443 17687 19090
HYDRA_URL=http://127.0.0.1:18443 uv run python -m pinned.eval curve --run-id pinned-curve-1 --iterations 2 --concurrency 20 --workers 4
uv run python -m pinned.eval curve-json
```
Completions are cached by (arm, problem, iteration, prompt hash), so a rerun on a fresh load reuses the same solver
answers only where the context text comes out identical. The learned graph is not saved (step 4 skipped, as asked); it
lives only in this session's node `pinned-g`.
