#!/usr/bin/env bash
# Make sure the Docker daemon is up. Claude cloud VMs ship dockerd but do not start it.
set -euo pipefail
docker info >/dev/null 2>&1 && exit 0
command -v dockerd >/dev/null || { echo "docker daemon is not running and dockerd is not installed" >&2; exit 1; }
SUDO=""; [ "$(id -u)" -eq 0 ] || SUDO="sudo -n"
$SUDO sh -c 'nohup dockerd >/var/log/dockerd.log 2>&1 &'
for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && exit 0; $SUDO docker info >/dev/null 2>&1 && exit 0; sleep 1; done
echo "dockerd did not start; see /var/log/dockerd.log" >&2; exit 1
