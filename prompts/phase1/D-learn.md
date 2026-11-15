# Phase 1 · Session D — the learning loop (Track 1's heart) + the ablation and control arms

You are session D of Phase 1 of **Pinned** (HydraDB hackathon, Track 1: "a graph search system that learns from its own
answers to improve what it ingests and how it queries"; fair game: path compression, edge reweighting, gap-driven
ingestion, entity split/merge; the bar: an accuracy curve rising over iterations). Pinned's graph holds every public
API across library versions; every time an agent's code passes or fails, the graph must change so the next answer is
better. You build that learner, exactly as PLAN.md section 4 describes.

Your folders: `src/pinned/learn/`, `tests/learn/`, `evidence/learn/`. Progress file: `docs/progress/D-learn.md`.
Export exactly `pinned.learn.GraphLearner(graph, ingestor=None, run_id="live", split_path=None)` implementing
`Learner`, and `pinned.learn.PlainStore` (see item 5).

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
2. Write `docs/progress/D-learn.md`: what is done (per numbered item), how to run it, test summary, every number with
   its evidence path, HydraDB findings, needs from main, known limits (honest).
3. Commit in clear steps and push your branch (never push to `main`). Your last message: the branch name and a
   one-paragraph summary.

Sessions B (ingest) and C (search) are built in parallel with exactly the schema in contract.py. Build your own fixture
graphs in tests with `pinned.graph` helpers using exactly that schema. For `fill_gap`, accept any object with the
`Ingestor` protocol (tests use a fake under `tests/`).

## Scope
1. **`record(episode)`** (one call per solver attempt):
   - refuse (raise a clear error) any episode whose `problem_id` is in the `"test"` list of `split_path`
     (`evidence/split.json`, written by session E: `{"seed", "train": [...], "test": [...]}`); `split_path=None`
     = live product use, no split check;
   - parse `episode.code` with ast to find which library APIs the code actually called; match them to
     `episode.context.items`; **edge reweighting**: the CUES / SHORTCUT / REPLACED_BY edges behind used items get +w on
     pass and −w on fail (bounded, documented formula; `set_edges_many` batched);
   - **gap-driven ingestion**: from `error_type`/`error_text` (AttributeError, ImportError/ModuleNotFoundError,
     TypeError "unexpected keyword argument", DeprecationWarning/FutureWarning text) extract the named module/attribute;
     if the graph had no or wrong facts for it at that version, call `ingestor.fill_gap(lib, version, hint)`;
   - **learned cues**: on a pass, add/raise `CUES{source: learned}` edges from the problem's words to the APIs the passing
     code used (problem text + passing code only; never the answer key);
   - provenance: an `Episode` node + `USED{helped}` edges to the APIs used.
2. **`maintain()`** (once per iteration):
   - **path compression**: a multi-hop chain (Term → Api −REPLACED_BY/ALIAS_OF→ … → Api) that led to passes ≥ 2 times
     becomes a direct `SHORTCUT{w, lo_ord, hi_ord, hops}` edge for the version range it was proven on;
   - **entity merge**: two Api nodes that are the same object (identical signature + docstring line across the same
     versions, or one re-exporting the other) → `ALIAS_OF`;
   - **entity split**: one path whose behaviour/signature differs across versions per the episodes (passes at one
     version, fails with a TypeError at another) → per-version `CHANGED_IN` facts so search answers per version;
   - **decay**: edges that keep leading to failures drop below `W_FLOOR` and stop being suggested.
   Return a `LearnReport` with real counts.
3. **Move log**: every move appends one JSON line to `evidence/learn/<run_id>.jsonl`:
   `{move, edge/node keys, before, after, why, episode ids}`.
4. **Product hook**: the same `GraphLearner` with `run_id="live"` is what the MCP tool `report_outcome` calls for real
   users; make sure an outcome with no context (agent did not ask Pinned first) still records what it can.
5. **Ablation + control arms** (used by the evaluation in Phase 2; build and test them now):
   - `PlainStore`: implements BOTH `Search` and `Learner` over plain Python dicts holding the SAME facts and the SAME
     learned signals (per library/version: which paths exist; word → path weights; episode outcomes) but NO traversal:
     no replacement chains, no aliases, no multi-hop. Built from a graph export (`PlainStore.from_graph(graph, library)`)
     so it starts from identical data. This answers the judges' "would it fall apart on a plain store?".
   - `ShuffledSearch(search, seed)`: negative control — wraps any `Search` and returns contexts with the same number of
     items but shuffled weights / random valid items, so a gain that survives shuffling is exposed.
6. **Tests** with fake episodes on fixture graphs: every move above changes the graph exactly as specified (weights
   move, SHORTCUT appears only after ≥ 2 passes and only for its range, ALIAS_OF merge, CHANGED_IN split, decay below
   floor, fill_gap called with the right hint), test-split episodes refused, move log written, PlainStore answers from
   dicts only, ShuffledSearch keeps size but breaks ranking.
