"""Build the real objects from the environment. The default factory wires the HydraDB graph to the parts built in
parallel (contract.py names them): pinned.search.GraphSearch, pinned.learn.GraphLearner, pinned.ingest.PyPIIngestor.

PINNED_FACTORY="module:callable" swaps in another builder with the same signature (settings -> (search, learner, graph)),
e.g. to serve a different graph backend; the app itself never changes.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

from pinned.contract import Graph, Learner, Search
from pinned.graph import HydraGraph

from .config import Settings
from .errors import ServiceMisconfigured
from .service import LIVE_RUN_ID, PinnedService

Built = tuple[Search, Learner, Graph]


def build_default(settings: Settings) -> Built:
    try:
        from pinned.ingest import PyPIIngestor
        from pinned.learn import GraphLearner
        from pinned.search import GraphSearch
    except ImportError as e:
        raise ServiceMisconfigured(
            f"a Pinned part is not installed ({e.name or e}); the server needs pinned.search, pinned.learn and "
            "pinned.ingest, or set PINNED_FACTORY to another builder"
        ) from e
    graph = HydraGraph(url=settings.hydra_url)
    settings.ingest_cache_dir.mkdir(parents=True, exist_ok=True)
    ingestor = PyPIIngestor(graph, cache_dir=settings.ingest_cache_dir)
    return GraphSearch(graph), GraphLearner(graph, ingestor=ingestor, run_id=LIVE_RUN_ID), graph


def load_factory(spec: str) -> Callable[[Settings], Built]:
    module, _, attr = spec.partition(":")
    if not module or not attr:
        raise ServiceMisconfigured(f"PINNED_FACTORY must look like 'package.module:function', got {spec!r}")
    try:
        fn: Any = getattr(importlib.import_module(module), attr)
    except (ImportError, AttributeError) as e:
        raise ServiceMisconfigured(f"cannot load PINNED_FACTORY {spec!r}: {e}") from e
    if not callable(fn):
        raise ServiceMisconfigured(f"PINNED_FACTORY {spec!r} is not callable")
    return fn


def build(settings: Settings | None = None) -> tuple[Built, Settings]:
    settings = settings or Settings.from_env()
    return load_factory(settings.factory)(settings), settings


def service_from_env(settings: Settings | None = None) -> PinnedService:
    (search, learner, graph), settings = build(settings)
    return PinnedService(search, learner, graph, settings)


def app_from_env():
    """ASGI app factory for uvicorn (`uvicorn --factory pinned.server.factory:app_from_env`)."""
    from .http_app import create_app

    (search, learner, graph), settings = build()
    return create_app(search, learner, graph, settings)
