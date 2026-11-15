#!/usr/bin/env bash
# Start a local HydraDB v0.4.0 dev node. Usage: infra/hydradb-up.sh [name] [http_port] [bolt_port] [admin_port]
# Each agent may run its own throwaway node on its own ports + folder (data in .hydradb/<name>, git-ignored).
set -euo pipefail
NAME=${1:-hydradb}; HTTP=${2:-8443}; BOLT=${3:-7687}; ADMIN=${4:-9090}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; S="$ROOT/.hydradb/$NAME"
"$ROOT/infra/ensure-docker.sh"
mkdir -p "$S/store" "$S/cache"
printf '%s\n' 'local-development-token-32-bytes' > "$S/auth-token"
chmod -R 777 "$S"   # the container user must write the cache dir
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --restart "${HYDRA_RESTART:-no}" --name "$NAME" -p "$HTTP:8443" -p "$BOLT:7687" -p "$ADMIN:9090" -v "$S:/data" \
  -e CLOUD_PROVIDER=local -e LOCAL_PATH=/data/store \
  -e GRAPH_NAMESPACE=default -e GRAPH_ID=default -e GRAPH_CELL_ID=cell-0 -e GRAPH_CELLS=cell-0 \
  -e GRAPH_NODE_ID=node-0 -e GRAPH_BOLT_NODE_ADDRESSES=node-0=127.0.0.1:7687 \
  -e GRAPH_ADVERTISED_BOLT_ADDR=127.0.0.1:7687 -e GRAPH_DATA_CACHE_DIR=/data/cache \
  -e GRAPH_AUTH_TOKEN_FILE=/data/auth-token -e GRAPH_ALLOW_PLAINTEXT=true \
  -e GRAPH_USAGE_REPORT_ENABLED=false -e RUST_MIN_STACK=33554432 \
  ghcr.io/hydra-db/hydradb:0.4.0 >/dev/null
for i in $(seq 1 30); do curl -sf "http://127.0.0.1:$ADMIN/readyz" >/dev/null 2>&1 && { echo "HydraDB '$NAME' ready: http://127.0.0.1:$HTTP"; exit 0; }; sleep 1; done
echo "HydraDB '$NAME' did not become ready; see: docker logs $NAME" >&2; exit 1
