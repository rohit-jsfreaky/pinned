"""Server settings, read once from the environment (and overridable in code / tests)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from None


def _list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if raw is None:
        return list(default)
    return [p.strip() for p in raw.split(",") if p.strip()]


@dataclass
class Settings:
    """Every knob of the product server. `Settings.from_env()` is what `pinned serve` / `pinned mcp` use."""

    hydra_url: str = "http://127.0.0.1:8443"
    # Browser origins allowed by CORS ("*" = any). Empty = no CORS headers at all.
    cors_origins: list[str] = field(default_factory=list)
    # Host header values the MCP endpoint accepts (DNS-rebinding guard). Empty = localhost only.
    allowed_hosts: list[str] = field(default_factory=list)
    # Requests per minute per client on /v1/* and /mcp. 0 disables the limit.
    rate_limit_per_min: int = 120
    # Trust X-Forwarded-For for the client address (only behind your own reverse proxy).
    trust_proxy: bool = False
    max_code_bytes: int = 100_000
    max_k: int = 32
    # Run learner.maintain() after this many live outcomes (0 = never from the server).
    maintain_every: int = 50
    evidence_dir: Path = ROOT / "evidence"
    ingest_cache_dir: Path = ROOT / "data" / "pypi-cache"
    # "module:callable" returning (search, learner, graph). Default builds the real HydraDB-backed parts.
    factory: str = "pinned.server.factory:build_default"

    @classmethod
    def from_env(cls) -> Settings:
        d = cls()
        return cls(
            hydra_url=os.environ.get("HYDRA_URL", d.hydra_url),
            cors_origins=_list("PINNED_CORS_ORIGINS", d.cors_origins),
            allowed_hosts=_list("PINNED_ALLOWED_HOSTS", d.allowed_hosts),
            rate_limit_per_min=_int("PINNED_RATE_LIMIT", d.rate_limit_per_min),
            trust_proxy=os.environ.get("PINNED_TRUST_PROXY", "").lower() in ("1", "true", "yes"),
            max_code_bytes=_int("PINNED_MAX_CODE_BYTES", d.max_code_bytes),
            max_k=_int("PINNED_MAX_K", d.max_k),
            maintain_every=_int("PINNED_MAINTAIN_EVERY", d.maintain_every),
            evidence_dir=Path(os.environ.get("PINNED_EVIDENCE_DIR", str(d.evidence_dir))),
            ingest_cache_dir=Path(os.environ.get("PINNED_INGEST_CACHE", str(d.ingest_cache_dir))),
            factory=os.environ.get("PINNED_FACTORY", d.factory),
        )
