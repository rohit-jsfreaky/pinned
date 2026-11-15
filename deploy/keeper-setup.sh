#!/usr/bin/env bash
# One-shot deploy of the hosted Pinned (graph + API + website) on an instabox machine (or any Linux box with Docker,
# Node 22, uv and sudo). Reads a GitHub token on stdin (needed while the repo is private). Idempotent: re-run to update.
#   gh auth token | instabox ssh keeper -- 'bash -s' < deploy/keeper-setup.sh
set -euo pipefail
read -r T
REPO=rohit-jsfreaky/pinned
DIR=/data/workspace/pinned
GRAPH_SHA=aced2bde35f64d22a8b50856b7ee196ab56da5a53f071cd07951642259ac84f5
mkdir -p /data/workspace
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull -q --ff-only; else
  git clone -q --depth 1 --single-branch -b main "https://x-access-token:$T@github.com/$REPO.git" "$DIR"; fi
git -C "$DIR" remote set-url origin "https://github.com/$REPO.git"   # do not keep the token on disk
cd "$DIR"

# 1. the graph: stream the 37 parts of branch graph-snapshot straight into the node's data folder, checking the sha256
NODE="$DIR/.hydradb/pinned-live"
if [ ! -d "$NODE/store" ]; then
  mkdir -p "$NODE"
  api() { curl -sfL -H "Authorization: Bearer $T" -H "Accept: application/vnd.github.raw" "https://api.github.com/repos/$REPO/$1"; }
  parts=$(curl -sfL -H "Authorization: Bearer $T" "https://api.github.com/repos/$REPO/git/trees/graph-snapshot" \
          | python3 -c 'import json,sys; print("\n".join(sorted(e["path"] for e in json.load(sys.stdin)["tree"] if ".part-" in e["path"])))')
  echo "graph: $(echo "$parts" | wc -l) parts"
  for p in $parts; do api "contents/$p?ref=graph-snapshot"; done \
    | tee >(sha256sum | cut -d' ' -f1 > /tmp/pinned-graph.sha) | tar -xz -C "$NODE"
  sleep 1; got=$(cat /tmp/pinned-graph.sha)
  [ "$got" = "$GRAPH_SHA" ] || { echo "graph sha256 mismatch: $got" >&2; rm -rf "$NODE"; exit 1; }
  echo "graph: sha256 ok"
fi
docker inspect pinned-live >/dev/null 2>&1 || HYDRA_RESTART=unless-stopped infra/hydradb-up.sh pinned-live 8443 7687 9090

# 2. the API (FastAPI + MCP) on 127.0.0.1:8000
uv sync -q
sudo tee /etc/systemd/system/pinned-api.service >/dev/null <<UNIT
[Unit]
Description=Pinned API
After=docker.service
[Service]
User=$(id -un)
WorkingDirectory=$DIR
Environment=HYDRA_URL=http://127.0.0.1:8443
Environment=PINNED_ALLOWED_HOSTS=*
Environment=PINNED_TRUST_PROXY=1
ExecStart=$(command -v uv) run pinned serve --host 127.0.0.1 --port 8000
Restart=always
[Install]
WantedBy=multi-user.target
UNIT

# 3. the website on :3000 (it forwards /v1 and /mcp to the API)
(cd web && npm ci --silent && npm run build >/dev/null)
sudo tee /etc/systemd/system/pinned-web.service >/dev/null <<UNIT
[Unit]
Description=Pinned website
After=pinned-api.service
[Service]
User=$(id -un)
WorkingDirectory=$DIR/web
Environment=PORT=3000
Environment=PINNED_API_ORIGIN=http://127.0.0.1:8000
ExecStart=$(command -v npm) start
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now pinned-api pinned-web
sudo systemctl restart pinned-api pinned-web
for _ in $(seq 1 60); do curl -sf localhost:3000/v1/libraries >/dev/null && break; sleep 2; done
curl -s localhost:3000/v1/libraries | python3 -c 'import json,sys; d=json.load(sys.stdin); print("live libraries:", len(d["libraries"]))'
instabox-port publish 3000 || true
instabox-port list || true
