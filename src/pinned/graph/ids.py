"""Stable integer ids and sortable version numbers shared by every part."""

import hashlib

from packaging.version import Version


def node_id(label: str, natural_key: str) -> int:
    """63-bit positive int from (label, key). Same input -> same id on every machine, every run."""
    digest = hashlib.sha1(f"{label}\x00{natural_key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF


_PRE = {"a": 100, "b": 300, "rc": 600}


def version_ord(version: str) -> int:
    """Sortable int for a PEP 440 version: 2.0.0 -> 2_000_000_999, 2.0.0rc1 -> 2_000_000_601, 1.21.6 -> 1_021_006_999.

    major*1e9 + minor*1e6 + micro*1e3 + stage (dev < a < b < rc < final=999). Minor/micro/pre numbers must be < 1000
    (and < 300 within a pre stage); anything larger raises instead of silently mis-ordering.
    """
    v = Version(version)
    rel = (list(v.release) + [0, 0, 0])[:3]
    if any(x >= 1000 for x in rel[1:]) or rel[0] >= 9_000_000:
        raise ValueError(f"version {version!r} does not fit version_ord")
    stage = 999
    if v.pre:
        kind, n = v.pre
        if n >= 300:
            raise ValueError(f"pre-release number too large in {version!r}")
        stage = _PRE[kind] + n
    elif v.dev is not None:
        stage = 0
    return rel[0] * 1_000_000_000 + rel[1] * 1_000_000 + rel[2] * 1_000 + stage
