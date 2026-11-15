#!/usr/bin/env bash
# When `claude -p` cannot run on the box that has the venvs: solve on one machine, test on the other.
# The run folder (evidence/runs/<run_id>/completions.jsonl) is the hand-off; commit/push it or copy it across.
#
#   machine with `claude` logged in (e.g. the laptop; needs only `uv sync` + the dataset):
#     scripts/cloud/split-solve-test.sh solve <run_id> <arm> [split]
#   machine with the venvs (cloud box after setup-eval.sh):
#     scripts/cloud/split-solve-test.sh test <run_id> [split]
#
# (On 15 Nov 2026 `claude -p` works inside the Claude cloud session itself, so both run on the cloud box.)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$ROOT"
STAGE=${1:?solve|test}; RUN=${2:?run_id}
case "$STAGE" in
  solve)
    ARM=${3:?arm: none|docs|pinned}; SPLIT=${4:-test}
    [ -f data/gitchameleon/DATASET_SHA ] || uv run python -m pinned.eval download
    [ "$ARM" = docs ] && uv run python -m pinned.eval fetch-docs --split "$SPLIT"
    uv run python -m pinned.eval solve --run-id "$RUN" --arm "$ARM" --split "$SPLIT" --concurrency 3 ;;
  test)
    SPLIT=${3:-test}
    uv run python -m pinned.eval test --run-id "$RUN" --split "$SPLIT" --workers 4
    uv run python -m pinned.eval curve-json ;;
  *) echo "usage: $0 solve <run_id> <arm> [split] | test <run_id> [split]" >&2; exit 2 ;;
esac
