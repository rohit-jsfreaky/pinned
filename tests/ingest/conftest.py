import pytest

from pinned.ingest import PyPIIngestor
from pinned.ingest.dataset import Pair

from .fakelib import FakeClient, LocalInspector, build


@pytest.fixture()
def fake(tmp_path, run_key):
    """(library name, import name, ingestor) for a fresh fake library on the shared test graph node."""
    pkg = f"fk{run_key}"
    dirs = build(tmp_path / "releases", pkg)
    return pkg, dirs


@pytest.fixture()
def ingestor(graph, fake, tmp_path):
    pkg, dirs = fake
    pairs = [Pair(pkg, "1.0.0", 1, ["3.12"], [""]), Pair(pkg, "1.2.0", 1, ["3.12"], [""])]
    inspector = LocalInspector(tmp_path / "work", dirs)
    ing = PyPIIngestor(graph, cache_dir=tmp_path / "cache", pairs=pairs, client=FakeClient(pkg, pkg, dirs),
                       inspector=inspector, report_dir=tmp_path / "reports")
    ing.test_inspector = inspector
    return ing
