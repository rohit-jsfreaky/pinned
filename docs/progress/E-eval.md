# Phase 1 · E — evaluation harness (GitChameleon 2.0) — DONE

Branch: `claude/optimistic-brahmagupta-whpupo`. Code: `src/pinned/eval/`. Tests: `tests/eval/`. Scripts: `scripts/cloud/`.
Evidence: `evidence/split.json`, `evidence/harness/`, `evidence/runs/baselines-p1/`, `evidence/curve.json`.

## Headline numbers (all from saved runs)
| what | number | evidence |
|---|---|---|
| dataset | 328 problems, HF commit `799a6a33e572a07a8985914e7251f5dea54b0ac4` | `evidence/split.json` |
| split | train 159 / test 169 (50/50 per library, seed 13) | `evidence/split.json` |
| venvs | 99/99 built from scratch, 117 s wall (4 workers), 27.9 GB apparent, 10.1 GB real disk (hard links) | `evidence/harness/venvs.json` |
| runner self-check | **328/328** ground-truth solutions pass (one fresh run, 155 s wall) | `evidence/harness/selfcheck.json` |
| `claude -p` in this cloud session | **works** (claude 2.1.325, `--model haiku` = `claude-haiku-5-5`) | `evidence/harness/solver-check.json` |
| baseline `none` on TEST | **pass@1 0.627** (106/169) | `evidence/runs/baselines-p1/summary.json` |
| baseline `docs` on TEST | **pass@1 0.675** (114/169) | same |
| solver cost | none $0.14, docs $0.22 (169 calls each, ~8 s per call) | `evidence/runs/baselines-p1/completions.jsonl` |

Re-running the test stage on the same completions gave identical pass/fail for all 338 rows (runner is deterministic).

## What is done (per scope item)
1. **Dataset + split.** `python -m pinned.eval download` snapshots the full HF repo into `data/gitchameleon/` and writes
   `DATASET_SHA`. `evidence/split.json` written ONCE and committed: per library, ids shuffled with `Random("13:<lib>")`,
   first half (rounded down) -> train, rest -> test. Odd-sized libraries give the extra problem to test, so it is 159/169.
   `write_split_once` refuses to overwrite with different content. `Problem.solver_view()` = exactly `problem`,
   `starting_code`, `library`, `version`, `python_version`, `additional_dependencies`. The answer key is only read in
   `answer_key.py` (hidden test path for the runner; ground truth for the self-check).
2. **Venvs + runner.** Read the official harness (`mrcabbage972/GitChameleonBenchmark` @ `3a1b604`: Dockerfile,
   `create_venvs.py`, `eval_sample.py`, `run_eval.py`) and reused its approach: same venv key and folder name
   (`gcham_venv_<sha256[:12]>`), same Pythons (3.7.17 / 3.9.19 / 3.10.14), same install order (`lib==ver` + extra deps,
   then pytest / pytest-cov / numpy / scipy pins per Python with upstream's substring rule), same test layout
   (`sample_<id>.py` + `test_sample.py`, pytest, exit code 0 = pass, 240 s timeout), OMP_NUM_THREADS=1, nltk data
   (punkt, wordnet, omw-1.4). Dedupe -> 99 venvs. CPU-only torch wheels (download.pytorch.org/whl/cpu; 1.4.0 to 2.0.0).
   Differences on purpose: `uv pip` (fast, hard links: 10 GB real instead of 28 GB), symlinked venvs (`--copies` breaks
   uv's relocatable CPython), and **era constraints** (below). The runner (`runner.py`) loads a tiny pytest plugin
   (`_plugin/_pinned_report.py`, Python 3.7-safe) that reports exception type + frames; `error_text` keeps ONLY frames
   from the solution file, unwraps pytest's CollectError, withholds assertion details raised by the hidden test, removes
   temp paths, and replaces any line that contains a hidden-test source line (>= 8 chars) with a marker. Tests run
   OFFLINE (proxy env -> closed local port) so network calls fail fast. Parallel (`execute.py`), resumable (run folder).
3. **Self-check (run).** 328/328 pass. First try was 287/328; every failure was an environment bug, fixed by capping a
   transitive dependency at what was current when the pinned library came out (`venvs.ERA_CONSTRAINTS`):
   flask 3.0.0 `werkzeug<3.1` · gradio 3.24.0 `gradio_client<0.1` · gradio 3.36.0 `gradio_client<0.3, fastapi<0.101,
   pydantic<2.1` · librosa 0.6.0 `joblib<0.12, numba<0.49` · librosa 0.7.0/0.7.1 `numba<0.49`. Problem 38 (gradio
   `launch(share=True)`) hung on the public tunnel; offline runs make gradio print "Could not create share link" and go on.
4. **Solver.** `claude -p` WORKS inside this cloud session (exact answer: yes; `evidence/harness/solver-check.json`).
   Command: `claude -p --model haiku --output-format json --tools "" --system-prompt <fixed> --no-session-persistence
   --setting-sources "" --strict-mcp-config --disable-slash-commands`, prompt on stdin, run in an empty temp dir = one
   fresh session, no tools, no CLAUDE.md / settings / MCP. One template for every arm (`solver.TEMPLATE`, hash
   `941c42962e2a`); the arm only fills the context block. Code = longest ```python block (else the reply).
   Cache: `data/eval_cache/completions/<key>.json`, key = sha256(arm, problem, iteration, prompt hash); reruns are free.
   Concurrency 3, shared back-off gate on rate limits (30 s doubling to 15 min, 10 tries), resumable. No rate limit was
   hit in 338 calls. The split laptop/cloud pipeline also exists (`solve` and `test` are separate commands joined by
   the run folder; `scripts/cloud/split-solve-test.sh`), but it is not needed here.
5. **Arms** (`arms.py`): `none`; `docs` = PLAIN-DOCS BASELINE: the problem's official `docs` URLs, fetched once
   (`docs.py`, raw body cached in `data/docs_cache/`, cleaned at read time: main content, no nav/scripts, GitHub `blob`
   URLs fetched as raw files, one meta-refresh hop), 8,000 chars per problem split across its URLs, labelled in the
   prompt; `GraphArm(search)` = any contract `Search` -> `ctx.to_prompt()` (structured Context kept for Episodes).
6. **Driver** (`driver.py`, tested with fakes + a real HydraDB node): iteration 0 = graph arm on TEST, no learning;
   iteration t = solve TRAIN with the current graph -> run -> `learner.record(Episode)` per train problem ->
   `learner.maintain()` once -> measure TEST. `GuardedLearner` raises `LeakError` for any non-train problem. Resumable:
   completions/results by row key, every record/maintain logged in `learn.jsonl` (a crash mid-learning never records twice).
7. **Outputs.** `evidence/runs/<run_id>/`: `config.json` (dataset sha, split, template hash, `claude --version`, flags,
   model alias + resolved model ids, arms, runner policy, git commit), `completions.jsonl` (code, raw reply, context
   text, cost, tokens), `results.jsonl` (problem, arm, split, iteration, passed, error_type, error_text, library,
   version, type_of_change), `summary.json` (pass@1 per arm x iteration x split, per library, per type_of_change, error
   types, missing, cost), `evidence/curve.json` (baselines + every graph-arm curve on TEST, built from runs).
8. **Baselines (run here, on TEST):** none 0.627, docs 0.675 (table above; per library / type in `summary.json`).
9. **Tests:** 44 in `tests/eval/` (split stability + stratification + write-once + matches the committed file; venv
   keys = upstream formula, dedupe, install order, torch index, era constraints; runner pass / hidden-assert withheld /
   solution-only frames / syntax + import errors / timeout / offline / no test-source leak; code extraction, cache keys,
   prompt has only solver fields, CLI output parsing, cache + back-off; arms; driver order, learner never sees test,
   crash-resume, outputs, curve; solve/test split stages). Whole suite: `uv run pytest -q` -> **59 passed**.

## How to run
Fresh Linux box: `scripts/cloud/setup-eval.sh` (apt libs, uv, docker, Pythons, dataset, venvs, self-check, solver
check). Baselines: `scripts/cloud/run-baselines.sh [run_id]`. Every step: `uv run python -m pinned.eval --help`:
`download | split | venvs | selfcheck | check-solver | fetch-docs | solve | test | baseline | summarize | curve-json |
curve`. Phase 2 curve: `HYDRA_URL=... uv run python -m pinned.eval curve --run-id curve-1 --iterations N` (imports
`pinned.search.GraphSearch`, `pinned.learn.GraphLearner`, `pinned.ingest.PyPIIngestor` by the contract names).

## HydraDB findings
None new. The only graph use in E is through `pinned.graph` helpers (`upsert_nodes`, label + property `MATCH`), all fine.

## Needs from main
1. `pyproject.toml`: add `[tool.pytest.ini_options] testpaths = ["tests"]`. Without it `uv run pytest -q` from the root
   collects `data/` (the dataset's own `hidden_tests/test_sample_*.py`, CPython 3.7's test suite) -> 736 errors. Until
   then the eval CLI writes `data/conftest.py` with `collect_ignore_glob = ["*"]` (any `python -m pinned.eval ...`).
2. `infra/ensure-docker.sh`: on this VM `dockerd` refused to start because of a stale `/var/run/docker.pid` (PID file of
   a process that no longer exists). Removing the file fixed it. Suggest: delete a stale pid file before starting.
3. Cloud environment setup script: add the apt packages from `scripts/cloud/setup-eval.sh` (build deps for CPython 3.7,
   libspatialindex, libsndfile, ffmpeg, gfortran, openblas) so Phase 2 sessions start ready.
4. Phase 2 needs `GraphLearner(graph, ingestor=..., run_id=..., split_path=...)` and `PyPIIngestor(graph,
   cache_dir=...)` exactly as the contract says; `pinned.eval curve` calls them that way.

## Known limits (honest)
- pass@1 is ONE sample per problem. `claude -p` has no temperature flag, so the CLI default sampling is used (recorded
  in config). A ~2-3 point gap (none vs docs = 8 problems) is within single-sample noise; Phase 2 should add repeats
  (e.g. 3 samples per problem via a sample index in the cache key) before claiming small gains.
- Haiku 5.5 was trained after GitChameleon 2.0 was published (Jul 2025), so contamination is possible; it affects all
  arms equally but inflates absolute numbers.
- Environment differs from upstream in: `uv pip` resolver, CPython 3.7.17 built from source (OpenSSL 3), symlinked
  venvs, 6 era constraints, offline test runs. Proof it is still right: 328/328 ground truth pass.
- Docs arm: 143 test URLs -> 124 fetched; 18 are dead links in the dataset (404), 1 GitHub releases page is blocked by
  this session's proxy (403; it would work on the laptop). Truncation keeps the start of each page (no relevance
  ranking) — a plain-docs baseline, by design. 168/169 test problems get some docs text.
- `error_text` policy: messages of exceptions raised in solution or library code are kept (scrubbed); assertion
  details raised by the hidden test are withheld. Messages like "f() missing 1 required positional argument" are kept.
- Graph arm and the curve are built and tested with fakes and a real HydraDB node, but not run on real data (Phase 2).
