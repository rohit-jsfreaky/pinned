"""Part B — ingestion: real library code from PyPI -> the HydraDB version graph.

`PyPIIngestor(graph, cache_dir=...)` implements `pinned.contract.Ingestor` (`ingest_version`, `fill_gap`).
CLI: `uv run python -m pinned.ingest --help`.
"""

from .ingestor import PyPIIngestor

__all__ = ["PyPIIngestor"]
