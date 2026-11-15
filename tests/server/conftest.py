"""Fixtures for the server tests: a seeded library on the real test HydraDB node, the app wired to the test stand-ins,
and a dead HydraDB address for the "graph down" cases."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from server_fakes import GraphBackedSearch, RecordingLearner, free_port, seed_graph

from pinned.graph import HydraGraph
from pinned.server import Settings, create_app


@pytest.fixture(scope="session")
def lib(graph) -> str:
    name = f"lib{uuid.uuid4().hex[:10]}"
    seed_graph(graph, name)
    return name


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(rate_limit_per_min=10_000, cors_origins=["https://app.example.com"], evidence_dir=tmp_path / "evidence",
                    maintain_every=0)


@pytest.fixture()
def learner(graph) -> RecordingLearner:
    return RecordingLearner(graph)


@pytest.fixture()
def app(graph, lib, learner, settings):
    return create_app(GraphBackedSearch(graph), learner, graph, settings)


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def dead_graph():
    g = HydraGraph(url=f"http://127.0.0.1:{free_port()}", retries=0, timeout=2)
    yield g
    g.close()


@pytest.fixture()
def down_client(dead_graph, settings):
    app = create_app(GraphBackedSearch(dead_graph), RecordingLearner(dead_graph), dead_graph, settings)
    with TestClient(app) as c:
        yield c
