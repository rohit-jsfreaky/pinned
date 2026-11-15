#!/usr/bin/env bash
# Remove a HydraDB node's data folder (.hydradb/<name>). The container writes files as its own user, so plain rm fails.
# Usage: infra/hydradb-rm-data.sh <name>
set -euo pipefail
NAME=${1:?usage: hydradb-rm-data.sh <name>}
case "$NAME" in */*|.|..|"") echo "bad name: $NAME" >&2; exit 1;; esac
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -d "$ROOT/.hydradb/$NAME" ] || exit 0
docker run --rm --user 0 --entrypoint sh -v "$ROOT/.hydradb:/d" ghcr.io/hydra-db/hydradb:0.4.0 -c "rm -rf '/d/$NAME'"
