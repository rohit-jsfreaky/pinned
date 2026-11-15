"""Pinned product server: HTTP API (FastAPI), MCP server (stdio + Streamable HTTP at /mcp), shared service layer."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pinned")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0"

from .config import Settings  # noqa: E402
from .errors import GraphUnavailable, NotFound, PinnedError  # noqa: E402
from .http_app import create_app  # noqa: E402
from .mcp_server import build_mcp  # noqa: E402
from .service import PinnedService  # noqa: E402

__all__ = [
    "GraphUnavailable", "NotFound", "PinnedError", "PinnedService", "Settings", "__version__", "build_mcp", "create_app",
]
