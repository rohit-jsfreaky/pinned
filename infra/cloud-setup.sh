#!/usr/bin/env bash
# Setup script for the Claude cloud environment (paste into the environment's "Setup script" box).
# Cloud VMs ship dockerd but do not start it, so start it, then cache the HydraDB image for every session.
set -euo pipefail
if ! docker info >/dev/null 2>&1; then
  nohup dockerd >/var/log/dockerd.log 2>&1 &
  for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 1; done
fi
docker pull ghcr.io/hydra-db/hydradb:0.4.0
