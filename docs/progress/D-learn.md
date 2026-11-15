# Phase 1 · D — learn (progress report)

Branch: `claude/elegant-sagan-1ws0cr`. Status: **all 6 scope items built and tested.** Whole suite: `uv run pytest -q` →
**65 passed** (50 in `tests/learn/`, 15 in `tests/graph/`), on a throwaway HydraDB 0.4.0 node from the `graph` fixture.

Exports (exactly as contract.py says):
- `pinned.learn.GraphLearner(graph, ingestor=None, run_id="live", split_path=None)` — implements `Learner`.
  One extra keyword-only arg: `log_dir=` (default: env `PINNED_LEARN_LOG_DIR`, else `./evidence/learn`).
- `pinned.learn.PlainStore` — implements `Search` and `Learner` over plain dicts (ablation arm).
- `pinned.learn.ShuffledSearch(search, seed, mode="items", universe=None)` — negative control.
- also `pinned.learn.TestSplitError`, `pinned.learn.SplitGuard`.

## Code map (`src/pinned/learn/`)
| file | what |
|---|---|
| `learner.py` | `GraphLearner`, `record()` (one `_Recorder` per episode), FIFO lock |
| `maintain.py` | `maintain()`: path compression, entity merge, entity split, decay; `MaintainState` |
| `weights.py` | the reweight formula, decay rule, thresholds (all numbers in one place) |
| `code.py` | ast: which library APIs the code really calls (imports, aliases, `x = np.linalg.norm`, `pd.DataFrame(d).append`) |
| `errors.py` | error text → the module/attribute it names (`Gap`) |
| `facts.py` | contract validity / signature / deprecation rule over ADDED_IN/REMOVED_IN/CHANGED_IN |
| `reads.py` | index-anchored edge reads (see HydraDB finding 1) |
| `routes.py` | node ids, which context items the code used, the REPLACED_BY/ALIAS_OF route behind an item |
| `movelog.py`, `split.py`, `text.py` | move log, train/test guard, problem words → Term keys |
| `plain.py`, `shuffled.py` | PlainStore, ShuffledSearch |

## What is done (per scope item)

### 1. `record(episode)`
- **Split guard**: `split_path` set → an episode whose `problem_id` is in `"test"` raises `TestSplitError` before anything
  is read or written (test: graph, Episode node and move log all unchanged). `split_path=None` = live use. Missing or
  malformed split file → clear error at construction.
- **What the code used**: `code.analyse` (ast) resolves calls through `import x as y`, `from a import b as c`, plain
  `import a.b`, simple assignments, and methods on a freshly built class instance. Import names come from
  `Library.import_names` (fallback: normalised library name, plus roots of the context item paths). Calls on receivers it
  cannot resolve (`df.append(...)`) match a `Class.method` context item by method name.
- **Edge reweighting** (formula in `weights.py`): for the context items the code used, the CUES and SHORTCUT edges from the
  problem's words to them (SHORTCUT only when `lo_ord <= ord(v) <= hi_ord`) and the REPLACED_BY edges along each item's
  chain get `w ← min(4.0, w + 0.25)` on pass, `w ← max(0.0, w − 0.35)` on fail. Bounded; BETA > ALPHA so a coin-flip
  edge drifts down; three straight fails take a default edge (1.0) below `W_FLOOR` (0.2). Each edge also counts
  `n_pass` / `n_fail`. Written with `set_edges_many` (batched per relationship).
- **Gap-driven ingestion**: `errors.find_gaps` reads AttributeError (module attr, `'X' object has no attribute`,
  `type object`), ImportError `cannot import name`, ModuleNotFoundError, TypeError `unexpected keyword argument` (incl.
  `Ridge.__init__()` / `DataFrame.to_csv()` qualnames), TypeError arity messages, Deprecation/FutureWarning lines (with the
  "use Y instead" replacement). Names resolve through the code's imports, else by asking the graph which candidate paths
  exist. Then the graph's facts at that version are checked: **no facts** (no Api node / no version edges / no
  signature) or **wrong facts** (graph says valid but runtime could not find it; graph signature has the rejected keyword;
  no deprecation fact) → `ingestor.fill_gap(lib, version, hint)`. If the graph already had it right (e.g. it says
  `removed`), nothing is ingested and a `gap_known` move is logged. Same (lib, version, hint) is filled once per learner;
  an ingestor exception is logged (`gap_failed`) and never loses the episode.
- **Learned cues** (pass only): from the first 24 problem words to every API the passing code used: existing CUES are
  raised (+ALPHA, source kept), missing ones are created as `CUES{w=0.6, source: learned, n_pass=1, n_fail=0}` (Term
  nodes upserted). Input = problem text + passing code only.
- **Provenance**: `Episode` node (key `<run_id>|<problem_id>|<iteration>`, contract props + `ord`, `error_api`, first 300
  chars of `error`, `with_context`) and `USED{helped}` edges to every used Api. `USED` also stores `via` (the chain
  `a>b>c` the item came by) and `cue_terms` (problem words that cue the chain's first API) — path compression reads them.
- Recording the same episode key twice is a no-op (safe for E's crash-resume).

### 2. `maintain()` → `LearnReport` with real counts
- **Path compression**: a chain Term → Api −REPLACED_BY/ALIAS_OF→ … → Api that led to ≥ 2 passing episodes (and more
  passes than failures on that chain) becomes `SHORTCUT{w, lo_ord, hi_ord, hops}` from that word to the last Api, with
  `lo/hi` = min/max version ord of the passing episodes. Existing shortcut → range widened, w kept at the max.
- **Entity merge**: same library, same last name, same kind (function/class), identical ADDED_IN/REMOVED_IN history with
  identical non-trivial signatures, AND (one path re-exports the other: its module is a parent package of the other's
  module) OR (both have the same non-empty `doc` line) → `ALIAS_OF{evidence}` re-export → definition. Siblings with only
  equal signatures are NOT merged (test).
- **Entity split**: per Api, versions where episodes passed vs. failed with a TypeError naming that Api; at every flip
  between neighbouring versions → `CHANGED_IN` at the upper version. `kind=signature` with the real before/after when the
  graph knows two different signatures, else `kind=behaviour` with "works at X" / "TypeError at Y: <message>". Skipped
  where the graph already has a signature/behaviour change in that range, or the Version node is missing.
- **Decay**: edges with `n_fail >= 3` and `n_fail > 2·n_pass` still at `w >= W_FLOOR` → `w = 0.1` (below the floor; search
  stops suggesting them).
- Incremental: merge reruns for a library only on the first call in a process or after a gap fill touched it; decay
  scans the whole node once per process, then only edges `record()` touched. (Reason: HydraDB finding 2.)

### 3. Move log
`evidence/learn/<run_id>.jsonl`, one line per move: `{ts, run_id, move, keys, before, after, why, episodes}`. Moves:
`reweight, learn_cue, gap_fill, gap_known, gap_open, gap_failed, episode, shortcut, merge, split, split_skipped, decay`.

### 4. Product hook
`GraphLearner(graph, ingestor, run_id="live")` is what F's `report_outcome` / `POST /v1/outcome` should call. Works with
`context=None` (learns from the APIs the code called: reweights cues from the task words to them, adds learned cues on a
pass, gaps, provenance) and with an empty `problem_id` (a stable `live-<hash>` id is made). Code that does not parse
still records provenance + gaps. Thread-safe: one record/maintain at a time, served in arrival order (FIFO lock).

### 5. Ablation + control
- `PlainStore.from_graph(graph, library_or_list)` exports the same facts (version events, signatures, deprecations,
  kinds, import names), the same signals (CUES weights + counts; SHORTCUT weights folded in as plain word→path weights)
  and episode outcomes into dicts, then never touches the graph again (test monkeypatches the graph to explode and
  runs every method). `context_for` = word weights / log(fan-out) + a bonus for paths in the starting code; status by
  the same contract rule; **no chains, no aliases, no multi-hop**: a removed API is reported as removed with no
  replacement. `check_calls`, `explain_api`, `record` (same formula, same learned cues), `maintain` (decay only).
  `refresh_facts(graph)` re-reads facts after gap fills but keeps its own learned weights (fair ablation). `save/load`
  JSON. `valid_paths(lib, version)`.
- `ShuffledSearch(search, seed)`: same number of items; `mode="weights"` = same items, scores permuted and re-ranked;
  `mode="items"` = random valid items (from `universe`, e.g. `PlainStore.valid_paths`, else from what the inner search
  returned so far), reproducible per (seed, problem). `check_calls` / `explain_api` pass through.

### 6. Tests (`tests/learn/`, 50)
`test_pure.py` (code analysis, every error shape, weight bounds, decay rule, terms, validity rule, routes, split guard),
`test_record.py` (pass/fail reweighting incl. REPLACED_BY chain, SHORTCUT only in range, learned cues pass-only,
test-split refused, fill_gap called with the right hint for 6 error shapes, not called when the graph knew, ingestor
failure + dedupe + no ingestor, provenance + idempotent re-record, no-context outcome, move-log line shape, method on
unresolved receiver, bad version, weights floor at 0), `test_maintain.py` (SHORTCUT only after ≥ 2 passes and only for
its range, not when failing as often, ALIAS_OF merge incl. a non-merge, CHANGED_IN split behaviour + signature, decay
below floor, incremental decay/merge, 8 concurrent records lose no update), `test_plain_shuffled.py` (same data,
dicts only, no traversal, check/explain/unknowns, ranking moves with learning, same weights as the graph learner on the
same episodes, split guard, decay, save/load, shortcuts folded, refresh, multi-library export, ShuffledSearch keeps size
and breaks ranking in both modes).

## How to run
```bash
uv run pytest tests/learn -q                       # throwaway node from the fixture
HYDRA_URL=http://127.0.0.1:18443 uv run pytest tests/learn -q   # or a running dev node
infra/hydradb-up.sh pinned-d-scale 28443 27687 29090
uv run python tests/learn/scale_run.py --url http://127.0.0.1:28443          # → evidence/learn/scale-fixture.{json,jsonl}
uv run python tests/learn/scan_profile.py --url http://127.0.0.1:28443 --library <lib> --container pinned-d-scale
```
Use in code:
```python
learner = GraphLearner(graph, ingestor=PyPIIngestor(graph, cache_dir=...), run_id="iter-run-1",
                       split_path="evidence/split.json")
learner.record(episode)        # per train attempt
learner.maintain()             # once per iteration
```

## Numbers (all from saved runs; SYNTHETIC fixture data — cost/mechanics, NOT accuracy)
Graph: 20,400 Api, 20 versions, 20,400 ADDED_IN, 2,000 REMOVED_IN, 1,779 REPLACED_BY, 60,000 CUES; 450 synthetic episodes.
Source: `evidence/learn/scale-fixture.json` (move log: `evidence/learn/scale-fixture.jsonl`, 2,636 lines).
- `record()` 1 client: p50 **76.9 ms**, p95 80.3 ms, 13.9 episodes/s, 13.5 graph requests per call, 0 errors.
- `record()` 8 clients: p50 548.7 ms, p95 588.3 ms, p99 600.3 ms, 14.4 episodes/s (serialised by the FIFO lock).
  Before the FIFO lock (plain `threading.Lock`) the same load had p99 23.5 s (starvation); before index-anchored reads
  p50 was 177 ms at 1 client. (Those earlier runs were overwritten; the code changes are in commits fb345ac / a8bf08c.)
- `maintain()`: first full pass 25.0 s (merge read of the library + whole-node decay scan), after 150 more episodes with
  gap fills 12.9 s (library dirty → merge reread), nothing new 0.61 s.
- Moves: 792 reweight, 729 learn_cue, 65 gap_fill (fill_gap calls), 60 gap_known, 162 shortcut, 360 merge, 18 split,
  450 episode. 360 of the 400 synthetic re-exports merged; the other 40 all point at an API that is removed later in the
  ladder while the re-export is not, so their histories differ and they are correctly not merged (checked on the node
  after the run: 400 re-exports, 360 with ALIAS_OF, 40 unmerged, all 40 with a removed target, 0 other).
- PlainStore export of that library: 34.6 s; its `context_for` p50 0.27 ms, p95 0.79 ms.
- HydraDB read shapes: `evidence/learn/hydradb-anchor-profile.json` (below).

## HydraDB findings (for the main session → `docs/hydradb-findings.md`)
1. **An edge read anchored only by `WHERE a.id IN $list` is a full scan of that edge type.**
   Query: `MATCH (a)-[r:CUES]->(b) WHERE a.id IN $s RETURN b.id AS d` (10 ids) → 70.5 ms median on a node with 60,729
   CUES; with `AND b.id IN $d` 56.9 ms. The server logs `"message":"query plan warrants attention","reason":"full_scan",
   "hydradb.query.access_path":"FullEdgeScan:CUES","rows_estimated":1000000`. Same pattern with a map anchor on one end
   — `MATCH (a)-[r:CUES]->(b {id: $x}) WHERE a.id IN $s` — 3.1 ms, and `MATCH (a {id: $x})-[r:CUES]->(b) WHERE b.id IN $d`
   2.5 ms. A node read `MATCH (a:Api) WHERE a.id IN $list` is fast (1.7 ms). Evidence:
   `evidence/learn/hydradb-anchor-profile.json`. Workaround: `pinned/learn/reads.py` sends one map-anchored request per id
   on the smaller side. **This also hits `pinned.graph.edges_from`** (it uses `WHERE a.id IN $ids`): fine on small nodes,
   but on the full graph every call scans the whole edge type. Ask upstream: use the id index for `a.id IN $list` on edge
   patterns (the node form already does).
2. **Library-filtered edge reads scan the whole edge type, and id chunks are slower.**
   `MATCH (a:Api)-[r:ADDED_IN]->(v) WHERE a.library = $lib RETURN …` → 5.7 s for 20,400 rows (same as unfiltered: 5.5 s);
   the same edges read as 21 chunks of `WHERE a.id IN $ids` (1000 ids) → 7.9 s; `MATCH (a:Api) WHERE a.library = $lib`
   → 2.2 s for 20,400 ids; `CUES` into one library (`(t)-[r:CUES]->(a:Api) WHERE a.library = $lib`) 9.3 s for 60,729 rows;
   whole-node `WHERE r.n_fail >= 3` on CUES 7.0 s. Workaround: maintain() rereads a library only when its facts changed
   (gap fill) and decays only touched edges after one full pass; PlainStore export reads all asked libraries in one scan
   per shape. Ask upstream: property index (or label+property anchor) for `a.library = $x` on edge patterns.
3. **UNWIND cannot drive a read.** `UNWIND $rows AS row MATCH (a {id: row.s})-[r:CUES]->(b) RETURN b.id AS d` →
   `invalid_request: ClientProtocol query is not supported yet: composite parameter $rows is only supported as an UNWIND
   input`; with a scalar list `UNWIND $ids AS i MATCH (a {id: i})-[r:CUES]->(b) RETURN …` → `row execution currently
   supports MATCH/WITH clauses followed by RETURN`. So a batched point-read of many edges needs one request per anchor
   (finding 1). Ask upstream: allow `UNWIND … MATCH … RETURN` for reads (it already works for writes).
4. (Confirms existing finding #2) each write request costs ~20 ms on this node; `record()` makes about 8 writes, which is
   most of its 77 ms.

## Needs from main
- **contract.py comments** (no code change needed): document the extra properties D writes, all scalars and ignored by
  search: `n_pass`, `n_fail` on CUES / SHORTCUT / REPLACED_BY; `via`, `cue_terms` on USED; `key`, `ord`, `error_api`,
  `error`, `with_context` on Episode. D also reads an optional `Api.doc` (docstring first line) for entity merge —
  **please ask B to store it as `Api.doc`** (B already collects it). Without it, merge still works on the re-export rule.
- **E (eval driver)**: (a) use one GraphLearner per run and a separate graph (node or copy) per arm — n_pass/n_fail live
  on edges; (b) never call `record` for test problems (the guard raises `TestSplitError`); (c) for a fair ablation,
  build `PlainStore.from_graph(graph, libs)` from the iteration-0 graph, feed it the same train episodes, and call
  `store.refresh_facts(graph)` after each iteration's gap fills; (d) negative control =
  `ShuffledSearch(GraphSearch(graph), seed, universe=store.valid_paths)`. Pass `episode.error_text` trimmed (never test
  source); the learner keeps only its first 300 chars.
- **C (search)**: learned splits are `CHANGED_IN{kind: behaviour}` with plain `before/after` text — show them as a note
  for that version. Learned cues are `CUES{source: learned}`. Term keys: D uses `pinned.learn.text.terms` (lower-case
  whole identifiers + dotted/snake/camel parts, stopwords out); C and B should produce the same keys.
- **F (server)**: `report_outcome` → `GraphLearner(graph, ingestor, run_id="live").record(Episode(..., context=None or
  the context it served, problem_id="" ok))`. One learner instance per process (it holds the lock and maintain state).
- **graph layer**: consider switching `edges_from` to the map-anchored shape (finding 1) before Phase 2's full graph.

## Known limits (honest)
- No accuracy numbers yet: the learning curve is Phase 2 (E drives, GitChameleon train/test). The numbers above are
  synthetic cost numbers only.
- "Used" is static: a call on an object we cannot type (`model.fit` where `model` came from a function) is matched to a
  context item only by method name; calls hidden behind `getattr` are invisible.
- Reweighting blames every used item for a failure even when the failure was the agent's logic (that is the spec; decay
  needs several failures before it acts).
- Learned cues add every non-stopword of the problem (first 24) — noisy by design; search's fan-out penalty keeps common
  words weak, and failed learned edges decay.
- One SHORTCUT edge per (word, Api): two proven ranges are merged into their hull.
- Entity split puts the change at the upper of two neighbouring observed versions; the true boundary may be earlier.
- Entity merge needs identical histories; a re-export added one version later than its target is not merged.
- `record()` is serialised per learner (one weight update at a time); across processes there is no lock, so two
  servers writing to the same node could lose an update.
- A crash in the middle of `record()` can leave some reweights written without the Episode node; re-recording that
  episode would then apply them twice.
- Merge/decay first passes scan whole edge types (HydraDB finding 2): on the full graph the first `maintain()` per
  process will take longer than the 25 s measured here.
