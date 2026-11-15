#!/usr/bin/env bash
# Fresh Linux box (Ubuntu 22.04/24.04, x86_64, root or sudo) -> everything the GitChameleon harness needs:
# system libs, Docker (for HydraDB tests), Python deps, CPython 3.7.17 / 3.9.19 / 3.10.14, the dataset, all 99 venvs,
# and the runner self-check. Safe to re-run: every step skips work already done.
#   scripts/cloud/setup-eval.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$ROOT"
SUDO=""; [ "$(id -u)" -eq 0 ] || SUDO="sudo"

echo "== system packages (same set as the upstream harness image + CPython build deps)"
$SUDO apt-get update -q
$SUDO apt-get install -y -q build-essential curl git libssl-dev zlib1g-dev libbz2-dev liblzma-dev libffi-dev \
  libsqlite3-dev libreadline-dev libncurses-dev tk-dev uuid-dev gfortran libopenblas-dev libspatialindex-dev \
  ffmpeg libsndfile1-dev xz-utils

echo "== uv + project deps"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv sync

echo "== docker + HydraDB image (tests start throwaway nodes)"
infra/ensure-docker.sh
docker pull -q ghcr.io/hydra-db/hydradb:0.4.0

echo "== interpreters"
uv python install 3.9.19 3.10.14
scripts/cloud/build-python37.sh

echo "== dataset, venvs, self-check"
[ -f data/gitchameleon/DATASET_SHA ] || uv run python -m pinned.eval download
uv run python -m pinned.eval venvs --workers 4          # -> evidence/harness/venvs.json
uv run python -m pinned.eval selfcheck --workers 4      # -> evidence/harness/selfcheck.json (expect 328/328)
uv run python -m pinned.eval check-solver               # -> evidence/harness/solver-check.json
