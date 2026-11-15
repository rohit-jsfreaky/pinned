# Phase 2 · Session B — ablation (plain store), negative control (shuffled), real-graph latency

You are session B of Phase 2 of Pinned (HydraDB hackathon, Track 1). Judges ask: "would it fall apart on a plain
store?" and want a control that attacks our own claim. Read CLAUDE.md, PLAN.md (section 5), docs/progress/E-eval.md,
docs/progress/D-learn.md and docs/progress/C-search.md first. A parallel session runs the main Pinned curve; you run the
comparisons with the SAME solver, model, template, concurrency and split. HARD DEADLINE: everything pushed within 35
MINUTES of starting. If a run is still going at 30 minutes, stop it, keep finished rounds, report honestly. Speed
over polish.

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
1. Wire the two arms into the eval CLI (small change in src/pinned/eval/__main__.py + a test): `curve --arm-kind
   plain|shuffled` (default `graph` = today's behaviour).
   - plain: `PlainStore.from_graph(g, library)` for every library in the dataset, used as BOTH search and learner in the
     same CurveDriver (it learns from its own TRAIN episodes exactly like the graph arm). Arm name `plain`.
   - shuffled: `ShuffledSearch(GraphSearch(g), seed=13)` with a GraphLearner, arm name `shuffled`.
2. Run both AT THE SAME TIME on the one loaded node: `curve --arm-kind plain --arm-name plain --run-id plain-curve-1
   --iterations 1 --concurrency 8` and `curve --arm-kind shuffled --arm-name shuffled --run-id shuffled-curve-1
   --iterations 0 --concurrency 8` (shuffled = iteration 0 only, measures TEST, writes nothing; plain learns into its
   own dicts, so one graph node is enough).
3. Real-graph latency (cheap, do it while the curves run): measure `context_for` and `check_calls` on the loaded real
   graph at concurrency 1/8/32 with `bench.load.run.measure`, using 100 real TEST problems' (problem, starting_code,
   library, version) as inputs. Save evidence/search/latency-real.json (p50/p95/p99, qps, errors, graph_queries).
4. `uv run python -m pinned.eval curve-json` at the end is NOT yours (session A rebuilds curve.json); just make sure your
   runs are under evidence/runs/ with summary.json.
5. Write docs/progress/P2-B-controls.md (numbers per arm and iteration, latency, every change, limits). Commit
   evidence/runs/plain-curve-1, evidence/runs/shuffled-curve-1, evidence/search/latency-real.json, code + tests, and the
   progress file on your branch; push. Never push to main.
Rules: never learn from TEST, never touch the answer key, no fabricated numbers.
