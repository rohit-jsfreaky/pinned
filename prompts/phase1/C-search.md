# Phase 1 · Session C — search (context_for, check_calls, explain_api) with real graph traversal

You are session C of Phase 1 of **Pinned** (HydraDB hackathon, Track 1). Pinned tells coding agents whether a call is
valid for the library version they actually have, and if not, walks a HydraDB graph to the right call for that version
and shows the chain (`DataFrame.append` -REMOVED_IN-> pandas 2.0 -REPLACED_BY-> `pandas.concat`). Judges check that the
graph does real work, so search must be real traversal, not lookups.

Your folders: `src/pinned/search/`, `tests/search/`, `evidence/search/`. Progress file: `docs/progress/C-search.md`.
Export exactly `pinned.search.GraphSearch(graph)` implementing `Search` from contract.py.

## Start
1. Read, in this order: `CLAUDE.md`, `PLAN.md`, `src/pinned/contract.py` (frozen; the schema, weights, validity rule and
   the class names every part exports), `docs/facts/hydradb.md`, `docs/hydradb-findings.md`, `docs/facts/gitchameleon.md`,
   and the graph layer you must use: `src/pinned/graph/` (+ `tests/graph/test_ops.py` shows every helper in use).
2. `uv sync`. Make sure the HydraDB image is local: `docker pull ghcr.io/hydra-db/hydradb:0.4.0`.
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
2. Write `docs/progress/C-search.md`: what is done (per numbered item), how to run it, test summary, every number with
   its evidence path, HydraDB findings, needs from main, known limits (honest).
3. Commit in clear steps and push your branch (never push to `main`). Your last message: the branch name and a
   one-paragraph summary.

Session B (ingest) builds the real graph in parallel with exactly the schema in contract.py. You do not wait for it:
build your own fixture graphs in tests with `pinned.graph` helpers, using exactly that schema (keys, props, `w` on every
CUES/SHORTCUT/REPLACED_BY edge, `sig` on ADDED_IN, before/after on CHANGED_IN, SHORTCUT lo_ord/hi_ord).

## Scope
1. **Validity + signature at a version**, exactly the rules written in contract.py (latest ADDED_IN/REMOVED_IN event
   at or before v; deprecated; added_later; missing; signature from CHANGED_IN/ADDED_IN). Batch it: the status of all
   candidates in a few requests (`edges_from` with `dst_props=("ord",)`), not one request per API.
2. **`context_for(problem, starting_code, library, version, k)`**:
   - candidates from the starting code (ast: imports + aliases resolved through `Library.import_names`, attribute
     chains, called names) and from the problem text (tokens → `Term` nodes → `CUES` edges with `w >= W_FLOOR`, plus
     `SHORTCUT` edges whose `lo_ord <= ord(v) <= hi_ord`), scored by the learned weights (so learning changes the answer;
     a term that cues many APIs counts less);
   - status of every candidate at v; for any that is not valid, walk `REPLACED_BY` / `ALIAS_OF` (`reach` / `multi_paths`
     — many seeds in one request) to the nearest API that IS valid at v, preferring higher `w`; the walk becomes the
     `chain` (Hop list incl. the REMOVED_IN/ADDED_IN version hops);
   - return a `Context` with up to k items (path, status, chain, signature at v, one plain note, score), `query_ms` and
     `graph_queries` (use the client's `.requests` counter). Unknown library/version → an empty Context with a clear note,
     never an exception.
3. **`check_calls(code, library, version)`**: parse with ast, resolve every call/attribute on the library through import
   aliases (`import numpy as np`, `from scipy import special`, `from x import y as z`), one `Finding` per call: status at
   v, a fix (the valid replacement from the chain) and the chain; also flag keyword arguments that do not exist in the
   signature at v (status `changed`, fix names the right parameter when the CHANGED_IN history shows a rename). Code that
   does not parse → a single clear Finding, no crash.
4. **`explain_api(library, path)`**: `ApiTimeline` over every ingested version (status + signature per version) plus the
   REPLACED_BY / ALIAS_OF chain out of it.
5. **CLI**: `uv run python -m pinned.search how "task text" --lib pandas --version 1.3.5`, `check file.py --lib --version`,
   `explain pandas pandas.DataFrame.append`. Plain readable output; `--json` for machines.
6. **Tests** on fixture graphs: removed→replaced chain (multi-hop, through ALIAS_OF), alias resolution, signature change
   per version, remove-then-re-add, added_later, deprecated, keyword-arg change in check_calls, import-alias resolution,
   ranking changes when a CUES `w` changes, a SHORTCUT applies only inside its ord range, edges below W_FLOOR ignored,
   unknown library/version.
7. **Latency (run it)**: build a fixture graph at realistic scale (≥ 20k Api, ≥ 20 versions per library, chains, terms)
   and measure `context_for` and `check_calls` at concurrency 1/8/32 with `bench.load.run.measure`; save
   `evidence/search/latency.json` (p50/p95/p99, qps, graph_queries per call). Keep graph round-trips per `context_for`
   small and report the number.
