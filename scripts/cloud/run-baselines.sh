#!/usr/bin/env bash
# The two baselines on the held-out TEST split, in one run folder: `none` (the agent alone) and `docs` (plain docs).
# Needs setup-eval.sh first and a logged-in `claude` CLI on this box. Resumable: re-run after any crash.
#   scripts/cloud/run-baselines.sh [run_id]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$ROOT"
RUN=${1:-baselines-p1}
uv run python -m pinned.eval fetch-docs --split test                       # docs pages fetched once, cached
uv run python -m pinned.eval baseline --run-id "$RUN" --arm none --split test --concurrency 3 --workers 4
uv run python -m pinned.eval baseline --run-id "$RUN" --arm docs --split test --concurrency 3 --workers 4 --offline-docs
uv run python -m pinned.eval curve-json                                    # -> evidence/curve.json
