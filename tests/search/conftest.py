"""Fixtures for search tests: a fresh fake library per test (builder in search_fixture_graph.py)."""

from __future__ import annotations

import pytest
from search_fixture_graph import LibBuilder

from pinned.search import GraphSearch


@pytest.fixture()
def lib(graph, run_key):
    """A fresh fake library builder: library name `lib<run_key>`, import name `m<run_key>`."""

    def make(versions=("1.0.0", "1.1.0", "2.0.0", "2.1.0", "3.0.0")) -> LibBuilder:
        return LibBuilder(graph, f"lib{run_key}", f"m{run_key}", list(versions))

    return make


@pytest.fixture(scope="session")
def search(graph):
    s = GraphSearch(graph)
    yield s
    s.close()
