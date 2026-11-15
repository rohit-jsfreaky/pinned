# Phase 1 · C — search: progress report

Branch: `claude/brave-cannon-wejqmm`. Status: **all 7 scope items done, tested, and run.**
Export: `pinned.search.GraphSearch(graph)` implements `Search` from contract.py.

## What is done (per scope item)

1. **Validity + signature at a version** — `src/pinned/search/validity.py`, exactly the contract rule: the latest
   ADDED_IN/REMOVED_IN at or before v decides (remove-then-re-add works; at equal ord ADDED_IN wins), `added_later`,
   `missing`, `deprecated` (a CHANGED_IN{kind=deprecated} after the latest ADDED_IN, at or before v), signature =
   latest CHANGED_IN{signature}.after, else the latest ADDED_IN.sig. Batched: **one request** gets the events of every
   candidate (`facts.events`: ADDED_IN UNION ALL REMOVED_IN UNION ALL CHANGED_IN with Version.ord, plus flags for
   REPLACED_BY/ALIAS_OF edges out and in). Not one request per API.
2. **`context_for`** — `search.py`:
   - candidates from the starting code (`code.py`: ast imports + aliases through `Library.import_names`, attribute
     chains, calls, simple instance types like `df = pd.DataFrame()` / `def f(df: pd.DataFrame)` /
     `pd.DataFrame(x).append`), from dotted names in the task text ("DataFrame.append", kept only if the graph knows
     them), and from task words → `Term` → `CUES` (w ≥ W_FLOOR) + `SHORTCUT` (lo_ord ≤ ord(v) ≤ hi_ord);
   - scored by the learned weights (`ranking.py`): Σ q(word)·w·idf(word) for CUES, 2·w for SHORTCUT, +3 for code
     references, ×1.5 for members of something the code uses. idf = 1/(1+ln(degree in this library)), so a word that
     cues many APIs counts less. Changing a `w` changes the answer (tested);
   - status of every shortlisted candidate at v; for anything not usable (removed / missing / added_later / deprecated)
     an `algo.SSpaths` walk over REPLACED_BY / ALIAS_OF **that also returns the version events of every node on the
     way** (one request per seed, seeds run in parallel). Seeds newer than v also get an incoming walk (who did the job
     before). The best replacement = the strongest API valid at v: Π w(REPLACED_BY) × 0.5^(hops−1), deprecated ×0.5,
     edges with w < W_FLOOR skipped. So the nearest wins unless learning says otherwise;
   - the walk becomes the chain: `old -REMOVED_IN-> lib 2.0.0 ; old -REPLACED_BY-> mid ; mid -ALIAS_OF-> new ;
     new -ADDED_IN-> lib 1.1.0`. Items whose candidate came from words start with up to 2 `word -CUES/SHORTCUT-> api`
     hops (note `w=…`) so the learner (D) can see which edges were behind an item. A replacement gets its own item
     (status valid, signature at v) right after the item it replaces;
   - `Context` with ≤ k items, `query_ms`, `graph_queries` (exact round trips of this call, counted per call so it
     stays right under concurrency; a test checks it equals the client's `.requests` delta at concurrency 1).
   - Status `renamed`: a removed API whose replacement is via ALIAS_OF, has the same last name in another module, or
     whose REPLACED_BY evidence says renamed/moved.
   - Unknown library / version / bad version string → a Context with ONE item, status `unknown`, and a plain note
     ("Pinned has no data for pandas==9.9. Ingested versions: …"). Never an exception. (Context has no note field, so
     the note rides on that single item.) A version is matched exactly, or by equal ord ("2.0" = "2.0.0").
3. **`check_calls`** — one `Finding` per call / attribute / `from x import y` on the library (resolved through
   `import numpy as np`, `import scipy.special`, `from scipy import special`, `from x import y as z`), with status at
   v, `fix` = the replacement from the chain, and the chain. Keyword arguments not in the signature at v → a Finding
   with status `changed`, call `path(kw=...)`: the fix names the right parameter when the CHANGED_IN history (or a
   re-add with a new sig) shows a rename in either direction (`use squeeze_me= instead of squeeze=`), else
   `remove kw=: not a parameter …; signature …`. `**kwargs` accepts anything; an unknown signature never raises false
   alarms. Code that does not parse → one Finding (status `unknown`, line of the error). A member reached only
   through an inferred instance type and unknown to the graph → status `unknown` (may be inherited), not `missing`.
4. **`explain_api`** — `ApiTimeline`: status + signature + a note (added / removed / deprecated / signature changed)
   at every ingested version, oldest → newest, plus the REPLACED_BY / ALIAS_OF chain out of it. 2 round trips (library
   + one SSpaths walk that carries the API's own events too).
5. **CLI** — `uv run python -m pinned.search how|check|explain …`, plain output, `--json` for machines.
6. **Tests** — 30 search tests (7 pure, 21 graph, 2 CLI), all on fixture graphs built with `pinned.graph` helpers in
   the contract schema (`tests/search/search_fixture_graph.py`). Every case the prompt lists is covered: multi-hop
   removed→replaced chain through ALIAS_OF, alias/rename, signature change per version, remove-then-re-add,
   added_later (walks back to the older API), deprecated, keyword-arg change both directions, import-alias resolution,
   ranking follows a changed CUES `w`, SHORTCUT only inside its ord range, CUES and REPLACED_BY below W_FLOOR ignored,
   unknown library/version, plus: higher-w replacement preferred, common words count less, member boost, task-text
   dotted names, hub words, walks skipped when no chain edges, round-trip counter, unparseable code, CLI errors.
7. **Latency (run)** — `evidence/search/latency.json` (see numbers below).

## How to run
```
uv sync && infra/ensure-docker.sh
uv run pytest tests/search -q                       # starts a throwaway HydraDB node (or uses HYDRA_URL)
infra/hydradb-up.sh cbench 28443 27687 29090
uv run python -m tests.search.bench_latency --url http://127.0.0.1:28443    # -> evidence/search/latency.json
uv run python -m tests.search.probe_hydradb --url http://127.0.0.1:28443    # -> evidence/search/hydradb-probes.json
HYDRA_URL=http://127.0.0.1:8443 uv run python -m pinned.search how "append rows" --lib pandas --version 2.0.0
uv run python -m pinned.search check file.py --lib pandas --version 2.0.0 [--json]
uv run python -m pinned.search explain pandas pandas.DataFrame.append
```

## Tests
`uv run pytest -q` (whole suite, throwaway node): **45 passed** (15 graph-layer + 30 search).

## Numbers (all from saved runs)
`evidence/search/latency.json` — synthetic graph built by the bench on a fresh node: 5 libraries × 24 versions,
22,000 Api, 74,223 CUES, 22,554 ADDED_IN, 3,083 REMOVED_IN, 3,495 CHANGED_IN, 3,969 REPLACED_BY, 1,193 ALIAS_OF,
1,000 SHORTCUT, 6,876 Term. Client and node on the same 4-vCPU cloud VM; HydraDB 0.4.0 (digest in the file).
400 calls per level, k=8, 0 errors at every level.

| call (warm) | clients | p50 ms | p95 ms | p99 ms | qps |
|---|---|---|---|---|---|
| context_for | 1 | 89.6 | 117.5 | 127.5 | 11.1 |
| context_for | 8 | 271.6 | 441.7 | 486.7 | 27.9 |
| context_for | 32 | 996.0 | 1386.5 | 1503.3 | 31.6 |
| check_calls | 1 | 21.9 | 39.4 | 50.0 | 42.0 |
| check_calls | 8 | 60.9 | 107.0 | 127.1 | 123.4 |
| check_calls | 32 | 283.6 | 559.5 | 626.2 | 102.7 |

- Graph round trips per call: context_for mean 6.11 (p50 6, min 4, max 11) warm; check_calls mean 3.56 (p50 3).
  Per-call count = client `.requests` delta in all 100 sequential calls (0 mismatches).
- Cold (first time a process sees the words, every word expanded once): context_for p50 171.8 ms, p95 712.5 ms,
  max 939.5 ms (`context_for_cold_first_100_sequential_ms`).
- "Warm" = the same GraphSearch instance has seen the vocabulary once (it remembers word degrees, see below). Above
  ~30 qps context_for the single node is saturated (latency grows, qps flat).
- An earlier run of the same bench, before the hub fix and walk skipping, had context_for p50 481 ms at 1 client and
  13 round trips; that file was overwritten by the final run, so treat that number as unverified history.

## Design note: hub words (why `facts.TermStats` exists)
HydraDB expands a node at ~0.11–0.15 ms per edge of the expanded type, and LIMIT does not reduce it
(`evidence/search/hydradb-probes.json`: 1,600-edge word = 176.9 ms for count(*), 184.8 ms with LIMIT 5; the same
node's 1 SHORTCUT edge = 2.2 ms). Reading CUES from the Api side is cheap (30 Apis = 16.9 ms). So per call only the
rarest words, within a budget of 250 CUES edges, are expanded to discover candidates; every other word is a "hub"
whose CUES weight is read from the Api side for the top 24 candidates. Learned weights on common words still count
(tested: decaying a hub's edge changes the ranking). `TermStats` remembers each word's CUES degree per process: a
cost hint and the idf denominator, never an answer. Every weight is read live from the graph on every call.

## HydraDB findings (for docs/hydradb-findings.md)
All in `evidence/search/hydradb-probes.json` (exact queries in `tests/search/probe_hydradb.py`).
1. **Expansion cost is linear in degree and LIMIT does not cut it.**
   `MATCH (t:Term {id: $t})-[r:CUES]->(a) RETURN count(*) AS n` → 10 edges 3.2 ms, 100 → 15.5 ms, 400 → 52.5 ms,
   1600 → 176.9 ms; `… RETURN a.id AS a ORDER BY a ASC LIMIT 5` on the 1600 node → 184.8 ms. Only edges of the
   pattern's type are paid (SHORTCUT from the same node: 2.2 ms). Workaround: hub words above (read from the low-degree
   side). Ask: degree / top-k by property without a full expansion, or an index on edge props.
2. **The first `IN` condition picks the anchor, not the cheapest one.**
   `MATCH (t)-[r:CUES]->(a) WHERE a.id IN $apis AND t.id IN $terms …` = 16.9 ms; the same with the conditions swapped
   = 206.3 ms (30 Apis, one 1600-edge word). Workaround: put the low-degree side first (commented in `facts.hub_cues`).
   Ask: pick the anchor by cardinality, or document that the first IN anchors.
3. **`sum()` rejects float properties.** `MATCH (t {id: $t})-[r:CUES]->(a) RETURN sum(r.w) AS s` →
   `invalid_request: OpenCypher query is not supported yet: sum aggregate requires integer values`. Workaround: sum
   client-side (we need per-edge rows anyway).
4. **Bound variable-length relationships fail; unbound work (with a fixed source id).**
   `MATCH (a {id: $s})-[r:CUES*1..2]->(b) RETURN b.id AS b` → `variable-length relationship bindings are not
   executable in Query engine`; without `r` it runs. With `WHERE a.id IN $ids` instead of a fixed id the bound form
   also fails. Workaround: `algo.SSpaths` (one per seed, parallel).
5. **Parameters are not allowed in RETURN, string literals are** (incl. inside UNION ALL):
   `RETURN $x AS x` → `RETURN currently supports <binding>.<property> or count(*)`; `RETURN b.id AS b, 'C' AS t
   UNION ALL …` works. We use literal tags to merge several relationship types into one request. (The error text is
   also wrong: literals are supported.)
6. `algo.MSpaths` selects targets with the same label as the sources (Api → Version targets return nothing), so
   "replacement chains + version events of every seed in one request" is not possible with it; SSpaths per seed is.
7. Infra (not HydraDB): on this cloud VM `infra/ensure-docker.sh` failed once with `failed to start daemon … process
   with PID 159 is still running` — a stale `/var/run/docker.pid` (no such process). `rm -f /var/run/docker.pid` and
   re-running fixed it. Suggest the script remove a stale pid file when `docker info` fails and the pid is dead.

## Needs from main
- None blocking. Nice to have (contract change, main decides): a `Term` property with its CUES degree (written by
  ingestion/learning), so a fresh process knows hub words without one cold expansion each.
- Note for B (ingest): edges dedupe on (src, rel, dst), so an Api can have only ONE CHANGED_IN per version. A release
  that both changes a signature and deprecates can keep only one `kind`. Search reads `kind=signature` for the
  signature and `kind=deprecated` for the status, so with one edge the other fact is lost. Main may want to decide this
  in the contract (e.g. one edge per kind on a helper node, or a `deprecated` flag on the signature edge).
- Note for D (learn): item chains start with `word -CUES-> path` / `word -SHORTCUT-> path` hops (src = the Term word)
  for the edges behind the item, then the version / REPLACED_BY / ALIAS_OF hops. REPLACED_BY hop notes start `w=…`.

## Known limits (honest)
- Instance types come only from constructor calls (capitalised class name) and annotations; `df = pd.read_csv(...)`
  gives no type (the graph has no return types), so `df.append` there is not checked by check_calls (context_for
  still uses `append` as a word). Unresolved method calls are never flagged, to avoid false alarms on e.g. `list.append`.
- Ranking constants (CODE_SCORE 3, SHORTCUT_BOOST 2, budget 250 edges, …) are set by reasoning, not tuned on real data
  yet; Phase 2 tunes them on the real graph.
- Latency is measured on a synthetic graph shaped like ours, not the real ingested graph (that is Phase 2). Client and
  server shared one 4-vCPU VM.
- `renamed` vs `removed` is a rule over edge type / names / evidence text, not a fact stored in the graph.
- Unknown library/version returns one `unknown` item (the note), not literally zero items.
