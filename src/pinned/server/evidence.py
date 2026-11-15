"""Read saved run evidence (latency benches, the learning curve) for /v1/stats and /v1/curve. Never invents numbers:
everything returned here is read from a file under evidence/, with the file path alongside."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import EvidenceUnreadable, NotFound

LATENCY_KEYS = ("concurrency", "calls", "errors", "qps", "p50_ms", "p95_ms", "p99_ms", "mean_ms", "graph_queries")


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise EvidenceUnreadable(f"cannot read {path.name}: {e}") from e


def latest_json(folder: Path) -> Path | None:
    """Newest *.json in a folder by modification time (bench files are timestamped, one per run)."""
    if not folder.is_dir():
        return None
    files = [p for p in folder.glob("*.json") if p.is_file()]
    return max(files, key=lambda p: (p.stat().st_mtime, p.name)) if files else None


def latency_records(data: Any, prefix: str = "") -> dict[str, list[dict[str, Any]]]:
    """Every latency record in a bench file, whatever its nesting: a dict holding `p50_ms` is one record, grouped by
    the key path that leads to it (e.g. "reads.cues_top20" -> one record per concurrency level)."""
    out: dict[str, list[dict[str, Any]]] = {}
    if isinstance(data, dict):
        if "p50_ms" in data:
            out.setdefault(prefix or "value", []).append({k: data[k] for k in LATENCY_KEYS if k in data})
            return out
        for k, v in data.items():
            for name, recs in latency_records(v, f"{prefix}.{k}" if prefix else str(k)).items():
                out.setdefault(name, []).extend(recs)
    elif isinstance(data, list):
        for v in data:
            for name, recs in latency_records(v, prefix).items():
                out.setdefault(name, []).extend(recs)
    return out


def latency_summary(evidence_dir: Path, part: str) -> dict[str, Any] | None:
    """Latest bench file of one part (`load` = graph layer, `search` = context_for/check_calls)."""
    path = latest_json(evidence_dir / part)
    if path is None:
        return None
    data = _load(path)
    return {
        "file": str(path.relative_to(evidence_dir.parent)) if path.is_relative_to(evidence_dir.parent) else str(path),
        "utc": data.get("utc") if isinstance(data, dict) else None,
        "latency": latency_records(data),
    }


def curve(evidence_dir: Path) -> dict[str, Any]:
    path = evidence_dir / "curve.json"
    if not path.is_file():
        raise NotFound(
            "no learning curve yet: evidence/curve.json is written by the evaluation run (Phase 2)",
            {"expected": "evidence/curve.json"},
        )
    data = _load(path)
    if not isinstance(data, (dict, list)):
        raise EvidenceUnreadable("curve.json must hold a JSON object or list")
    return {"file": "evidence/curve.json", "curve": data}
