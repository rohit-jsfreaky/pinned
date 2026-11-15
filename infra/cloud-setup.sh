#!/usr/bin/env bash
# Setup script for the Claude cloud environment (paste into the environment's "Setup script" box).
# Runs once per cached environment; keeps the HydraDB image on disk for every session.
set -euo pipefail
docker pull ghcr.io/hydra-db/hydradb:0.4.0
