# Phase 1 · Session E — evaluation harness (GitChameleon 2.0): dataset, split, venvs, runner, solver, arms

You are session E of Phase 1 of **Pinned** (HydraDB hackathon, Track 1; the bar is an accuracy curve rising over
learning iterations, with a clear baseline, measured on held-out problems). Pinned gives coding agents version-correct
API facts from a HydraDB graph that learns from pass/fail. You build the harness that measures it honestly on
GitChameleon 2.0 (328 version-pinned Python problems with hidden tests). Phase 2 runs the full curve on it.

Your folders: `src/pinned/eval/`, `tests/eval/`, `scripts/cloud/`, `evidence/split.json`, `evidence/runs/`,
`evidence/harness/`. Progress file: `docs/progress/E-eval.md`.

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
2. Write `docs/progress/E-eval.md`: what is done (per numbered item), how to run it, test summary, every number with
   its evidence path, HydraDB findings, needs from main, known limits (honest).
3. Commit in clear steps and push your branch (never push to `main`). Your last message: the branch name and a
   one-paragraph summary.

## Scope
1. **Dataset**: download the full HF dataset `cabbage972/GitChameleon-2.0` into `data/gitchameleon/` (git-ignored),
   record the dataset commit sha. Write `evidence/split.json` ONCE (and commit it): 50/50 stratified by library,
   seed 13, `{"seed": 13, "dataset_sha", "train": [ids], "test": [ids]}`. The solver sees ONLY `problem`,
   `starting_code`, `library`, `version`, `python_version`, `additional_dependencies` (+ the arm's context).
2. **Venvs + runner**: read the official harness (github.com/mrcabbage972/GitChameleonBenchmark: Docker image,
   `create_venvs.py`, `evaluate`) and reuse its approach rather than reinventing; dedupe venvs by (python_version,
   library==version, additional_dependencies); CPU-only torch wheels. Build ALL of them here (record sizes and time).
   The runner executes one candidate solution against that problem's hidden test in its venv with a timeout and returns
   pass/fail + `error_type` + `error_text` trimmed to the exception and frames from the SOLUTION file only (never the
   hidden test source). Parallel, resumable.
3. **Runner self-check (run it)**: run the official ground-truth solutions through your runner for all 328 problems.
   This is the ONLY permitted use of the answer key and it never touches a prompt or the graph. Expect ~100% pass;
   every failure is an environment bug to fix or an honest listed exception. Save `evidence/harness/selfcheck.json`.
4. **Solver**: `claude -p --model haiku --output-format json` with the prompt on stdin (check `claude --help` of the
   installed version for exact flags), one fresh session per problem, no tools, answer = code only (extract the code
   block). One prompt template for every arm; record `claude --version`, model id and template hash in each run's
   config. Cache every completion on disk keyed by (arm, problem, iteration, prompt hash); reruns are free. Concurrency
   2–3, back off and resume on rate limits. FIRST find out if `claude -p` works inside this cloud session and write the
   exact answer in your progress file. If it does not, split the pipeline into `solve` (runs wherever `claude` is logged
   in, e.g. Rohit's laptop, writes completions into the run folder) and `test` (runs here, reads them), and give the
   exact commands for each side.
5. **Arms**: an `Arm` interface that turns a problem into context text: `none` (no context), `docs` (text of the
   problem's official `docs` URLs, fetched once, cleaned, truncated to a fixed budget, cached under `data/`; clearly
   labelled as the plain-docs baseline), and `graph(search)` taking any `Search` (contract) → `ctx.to_prompt()`.
6. **The iteration driver** (built and tested now with fakes; the real run is Phase 2): iteration 0 = graph arm on the
   test split with no learning; iteration t = solve TRAIN with the current graph → run → `learner.record(Episode)` for
   each (never for test problems) → `learner.maintain()` → measure TEST with the frozen graph → one curve point. Every
   arm uses the same model, temperature and template. Resumable after a crash.
7. **Output format**: `evidence/runs/<run_id>/` with `config.json`, `completions.jsonl`, `results.jsonl` (problem, arm,
   iteration, passed, error_type), `summary.json` (pass@1 per arm and iteration, per library, per `type_of_change`), and
   `evidence/curve.json` built from runs; charts later. `uv run python -m pinned.eval ...` CLI for every step.
8. **Run the two baselines on the TEST split now** (`none` and `docs`), if the solver works here (else on the laptop
   side per item 4 and say so). These are real early numbers. `scripts/cloud/` = exact steps to run everything on a
   fresh Linux box.
9. **Tests**: split stability, venv dedupe keys, runner pass/fail/timeout/error trimming (no test source leaks),
   solver cache keys + code extraction, arms, driver order with fake Search/Learner (learner never sees test).
