"""Shared fixtures. `graph` = a REAL HydraDB node for the whole test session.

- HYDRA_URL set -> use that node (tests write under unique keys, so a shared dev node is fine).
- otherwise -> start a throwaway node on free ports with infra/hydradb-up.sh and remove it (and its data) afterwards.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import uuid
from pathlib import Path

import pytest

from pinned.graph import HydraGraph

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def graph():
    if os.environ.get("HYDRA_URL"):
        g = HydraGraph()
        if not g.ready():
            pytest.fail(f"HYDRA_URL={g.url} is set but the node is not answering /healthz")
        yield g
        g.close()
        return
    if not shutil.which("docker"):
        pytest.skip("needs docker (or HYDRA_URL pointing at a running HydraDB)")
    name = f"pinned-test-{uuid.uuid4().hex[:8]}"
    http, bolt, admin = _free_port(), _free_port(), _free_port()
    subprocess.run([str(ROOT / "infra/hydradb-up.sh"), name, str(http), str(bolt), str(admin)], check=True)
    g = HydraGraph(url=f"http://127.0.0.1:{http}")
    try:
        yield g
    finally:
        g.close()
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        subprocess.run([str(ROOT / "infra/hydradb-rm-data.sh"), name], capture_output=True)


@pytest.fixture()
def run_key():
    """Unique prefix so tests never collide with each other or with earlier runs on a shared node."""
    return uuid.uuid4().hex[:10]
