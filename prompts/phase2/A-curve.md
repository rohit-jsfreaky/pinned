# Phase 2 · Session A — the Pinned learning curve on the real graph

You are session A of Phase 2 of Pinned (HydraDB hackathon, Track 1; the bar: an accuracy curve rising over learning
iterations, measured on held-out problems). Read CLAUDE.md, PLAN.md (sections 4-5), docs/progress/E-eval.md and
docs/progress/D-learn.md first. HARD DEADLINE: everything pushed within 2 hours of starting; if a step is still
running at 1h45m, stop it, keep every finished round, and report honestly.

## Load the real graph (do this first; do NOT re-ingest)
The full graph (26 libraries, 282 versions, ~1.01M Api nodes, ~4.64M edges) is saved on branch `graph-snapshot` as 37
parts + SHA256SUMS (sha256 of the whole file: aced2bde35f64d22a8b50856b7ee196ab56da5a53f071cd07951642259ac84f5).
```bash
uv sync && infra/ensure-docker.sh && docker pull ghcr.io/hydra-db/hydradb:0.4.0
git fetch origin graph-snapshot && mkdir -p data/snap && git --work-tree=data/snap checkout origin/graph-snapshot -- . && git reset -q
cat data/snap/pinned-graph.tgz.part-* > data/pinned-graph.tgz && rm -rf data/snap
echo "aced2bde35f64d22a8b50856b7ee196ab56da5a53f071cd07951642259ac84f5  data/pinned-graph.tgz" | sha256sum -c
infra/hydradb-snapshot.sh load data/pinned-graph.tgz pinned-g 18443 17687 19090 && export HYDRA_URL=http://127.0.0.1:18443
```
Check it loaded: `uv run python -m pinned.search explain pandas pandas.DataFrame.append` must show versions.
Then `uv run python -m pinned.eval download` and build the venvs (see docs/progress/E-eval.md, "How to run").

## Do
1. Smoke test first (5 minutes max): `uv run python -m pinned.eval curve --run-id smoke --iterations 1` restricted to
   a handful of problems if the CLI allows it, else run `solve`/`test` for 3 TEST problems with `--arm pinned`. Fix only
   real bugs that block the run (in src/pinned/eval, search, learn — keep fixes small, tested, and listed).
2. The real run: `uv run python -m pinned.eval curve --run-id pinned-curve-1 --iterations 3 --concurrency 5 --workers 4`
   (iteration 0 = static graph on TEST, then learn on TRAIN → measure TEST, 3 times). It is resumable: if it crashes,
   rerun the same command.
3. `uv run python -m pinned.eval curve-json` → evidence/curve.json (keeps the existing none/docs baselines).
4. Save the learned graph too: `infra/hydradb-snapshot.sh save pinned-g data/pinned-graph-learned.tgz`, split it into
   90 MB parts and push them on a NEW orphan branch `graph-learned` (only the parts + SHA256SUMS), like graph-snapshot.
5. Write docs/progress/P2-A-curve.md: the curve numbers per iteration (pass@1, n), per library, what learning changed
   (counts from evidence/learn/*.jsonl and LearnReports), every fix you made, limits. Commit evidence/runs/pinned-curve-1,
   evidence/curve.json, evidence/learn and the progress file on your branch and push. Never push to main.
Rules: never learn from TEST (the driver enforces it), never touch the answer key, no fabricated numbers.
