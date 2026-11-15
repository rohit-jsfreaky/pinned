# Phase 1 · Session B — ingestion (library APIs across versions → the version graph)

You are session B of Phase 1 of **Pinned** (HydraDB hackathon, Track 1). Pinned tells coding agents whether a call is
valid for the library version they actually have, and if not, walks a HydraDB graph to the right call for that version.
Your job: turn REAL library code from PyPI into that graph, for every library version GitChameleon 2.0 uses.

Your folders: `src/pinned/ingest/`, `tests/ingest/`, `evidence/ingest/`. Progress file: `docs/progress/B-ingest.md`.
Export exactly `pinned.ingest.PyPIIngestor(graph, cache_dir=...)` implementing `Ingestor` from contract.py.

## Start
1. Read, in this order: `CLAUDE.md`, `PLAN.md`, `src/pinned/contract.py` (frozen; the schema, weights, validity rule and
   the class names every part exports), `docs/facts/hydradb.md`, `docs/hydradb-findings.md`, `docs/facts/gitchameleon.md`,
   and the graph layer you must use: `src/pinned/graph/` (+ `tests/graph/test_ops.py` shows every helper in use).
2. `uv sync`. Start Docker if it is not running (cloud VMs do not start it): `infra/ensure-docker.sh`. Make sure the
   HydraDB image is local: `docker pull ghcr.io/hydra-db/hydradb:0.4.0`.
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
2. Write `docs/progress/B-ingest.md`: what is done (per numbered item), how to run it, test summary, every number with
   its evidence path, HydraDB findings, needs from main, known limits (honest).
3. Commit in clear steps and push your branch (never push to `main`). Your last message: the branch name and a
   one-paragraph summary.

## Scope
1. **Pairs.** Download ONLY `dataset.jsonl` from HF dataset `cabbage972/GitChameleon-2.0` (huggingface_hub
   `hf_hub_download(..., repo_type="dataset")`, record the dataset commit sha) into `data/` (git-ignored). Use only the
   fields `library`, `version`, `python_version`, `additional_dependencies`. Save the 81 (library, version) pairs to
   `evidence/ingest/pairs.json`.
2. **Version ladders.** For each library, list releases from PyPI's JSON API (`https://pypi.org/pypi/<name>/json`):
   final releases only (skip pre/dev/yanked). Ladder = every exact version the dataset needs + the latest patch of
   every minor from one minor below the oldest needed to one minor above the newest needed. Write `Library` (with
   `import_names` from the wheel's top-level packages), `Version` (ord via `pinned.graph.version_ord`, `released`),
   `HAS_VERSION` and `NEXT` edges. Save ladders to `evidence/ingest/ladders.json`.
3. **Load each version's public API.** Download the wheel (prefer pure-python; else a manylinux x86_64 CPython wheel;
   else the sdist) into `data/pypi-cache/`, extract only `.py`/`.pyi` files, delete the archive (disk is 30 GB; torch is
   huge). Load STATICALLY with griffe (read griffe's current official docs first). Many APIs live in compiled
   extensions (e.g. `scipy.special` ufuncs, numpy, torch C ops) that static loading cannot see: detect those modules
   and fill them by griffe's dynamic inspection inside a throwaway `uv` venv for that exact version and Python
   (dataset `python_version`, deps from `additional_dependencies`), then delete the venv. Collect per public object:
   dotted path (starting with the import name), kind, signature string with params and defaults, docstring first line,
   alias/re-export target, and deprecation evidence (`warnings.warn(..., DeprecationWarning/FutureWarning)` messages,
   `@deprecated` decorators, `.. deprecated::` docstring directives).
4. **Diff consecutive ladder versions → edges** (schema and props exactly as contract.py):
   `ADDED_IN{evidence, sig}` (first appearance AND every re-appearance), `REMOVED_IN{evidence}`,
   `CHANGED_IN{kind: signature|deprecated|behaviour, before, after, evidence}`, `REPLACED_BY{w=W_DEFAULT, evidence,
   since_ord}` from deprecation text ("use X instead", "renamed to X", "moved to X", resolved to real Api paths) and
   from same-object moves between modules, `ALIAS_OF` (re-export → canonical public path), `MEMBER_OF`
   (method → class → module), `Term -CUES{w=W_DEFAULT, source: name|doc}-> Api` from name parts (split dotted /
   snake / camel) and docstring words (stopwords removed, lower-case). Every CUES/REPLACED_BY edge carries `w`.
   Use `upsert_nodes` / `link_many` / `link_props_many` (batched). Re-running a version adds nothing new.
5. **`fill_gap(library, version, hint)`**: targeted deeper ingest when learning reports a failure naming `hint`
   (a module or attribute): resolve the module, walk all its members incl. re-exports, dynamic inspection, deprecation
   messages; add the missing facts; return an `IngestReport`. Must be safe to call repeatedly.
6. **CLI**: `uv run python -m pinned.ingest --all`, `--lib scipy --version 1.7.3`, `--ladder scipy`, `--gap scipy
   1.7.3 scipy.special.i1`. Progress output + one `IngestReport` JSON per version in `evidence/ingest/`.
7. **Tests** (`tests/ingest/`) with tiny fixture packages you build in tmp dirs (two+ versions of a fake library):
   removed function, rename with a "use X instead" warning, signature change, alias/re-export, remove-then-re-add,
   compiled-member fallback path, idempotent re-run, fill_gap adds a fact.
8. **Run it for real**: ingest ALL 81 pairs + ladders into a local node in this session (small libraries first,
   torch/librosa last). Save `evidence/ingest/summary.json`: per library and total — versions, Api nodes, edges per type,
   seconds, rows/s. Coverage diagnostic ONLY (never used to decide what to ingest): what share of the dataset's
   `api_calls` paths exist as Api nodes valid at the problem's version — report it per library, with the misses listed.
