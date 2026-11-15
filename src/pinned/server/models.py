"""Pydantic request / response models of the HTTP API and the MCP tools' structured content.

Static shape limits live here; limits that depend on settings (code bytes, k range) are enforced by PinnedService so
every surface (HTTP, MCP, CLI) applies the same ones.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pinned.contract import ApiTimeline, Context, Finding, Hop, Status

from .service import LibraryInfo, OutcomeResult

LIB = Field(min_length=1, max_length=100, description="PyPI project name, e.g. pandas or scikit-learn", examples=["pandas"])
VER = Field(min_length=1, max_length=64, description="The exact installed version, e.g. 2.0.3", examples=["2.0.3"])
CODE = Field(description="Python source using the library", examples=["import pandas as pd\ndf.append(row)"])


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class CheckIn(_In):
    code: str = CODE
    library: str = LIB
    version: str = VER


class HowIn(_In):
    task: str = Field(min_length=1, max_length=4000, description="What the code should do, in plain words")
    code: str = Field(default="", description="Optional current code, so its calls are taken into account")
    library: str = LIB
    version: str = VER
    k: int = Field(default=8, description="How many facts to return (1..max_k, default max_k 32)")


class OutcomeIn(_In):
    code: str = CODE
    library: str = LIB
    version: str = VER
    passed: bool = Field(description="True if the code ran and its tests passed")
    error_type: str = Field(default="", max_length=100, description="e.g. AttributeError, TypeError, ImportError")
    error_text: str = Field(default="", max_length=20000, description="Trimmed error message / traceback")
    task: str = Field(default="", max_length=4000, description="The task the code was written for (same as in /v1/how)")


# ---------------------------------------------------------------------------------------------------- outputs
class HopOut(BaseModel):
    src: str
    rel: str
    dst: str
    note: str = ""


class FindingOut(BaseModel):
    call: str
    status: Status
    fix: str = ""
    chain: list[HopOut] = []
    line: int | None = None


class CheckOut(BaseModel):
    library: str
    version: str
    ok: bool = Field(description="True when every call is valid at this version")
    findings: list[FindingOut]
    took_ms: float = 0.0


class ContextItemOut(BaseModel):
    library: str
    path: str
    status: Status
    chain: list[HopOut] = []
    signature: str = ""
    note: str = ""
    score: float = 0.0


class HowOut(BaseModel):
    library: str
    version: str
    items: list[ContextItemOut]
    prompt: str = Field(description="The same facts as one plain-text block, ready to paste into a prompt")
    query_ms: float = 0.0
    graph_queries: int = 0
    took_ms: float = 0.0


class VersionFactOut(BaseModel):
    version: str
    ord: int
    status: Status
    signature: str = ""
    note: str = ""


class TimelineOut(BaseModel):
    library: str
    path: str
    kind: str = ""
    versions: list[VersionFactOut]
    replaced_by: list[HopOut] = []
    graph_queries: int = 0
    took_ms: float = 0.0


class LearnReportOut(BaseModel):
    edges_reweighted: int = 0
    shortcuts_added: int = 0
    gaps_ingested: int = 0
    merged: int = 0
    split: int = 0
    notes: list[str] = []


class OutcomeOut(BaseModel):
    recorded: bool = True
    episode_id: str
    used_context: bool = Field(description="True when the context Pinned gave for this task was linked to the outcome")
    maintain_scheduled: bool = False
    learned: LearnReportOut
    took_ms: float = 0.0


class VersionOut(BaseModel):
    version: str
    ord: int
    released: str = ""


class LibraryOut(BaseModel):
    name: str
    import_names: list[str] = []
    versions: list[VersionOut]


class LibrariesOut(BaseModel):
    libraries: list[LibraryOut]
    took_ms: float = 0.0


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = {}


class ErrorOut(BaseModel):
    error: ErrorBody


# ---------------------------------------------------------------------------------------------------- converters
def _hops(hops: list[Hop]) -> list[HopOut]:
    return [HopOut(**asdict(h)) for h in hops]


def check_out(library: str, version: str, findings: list[Finding], took_ms: float = 0.0) -> CheckOut:
    return CheckOut(
        library=library,
        version=version,
        ok=all(f.status == "valid" for f in findings),
        findings=[FindingOut(call=f.call, status=f.status, fix=f.fix, chain=_hops(f.chain), line=f.line) for f in findings],
        took_ms=took_ms,
    )


def how_out(ctx: Context, took_ms: float = 0.0) -> HowOut:
    return HowOut(
        library=ctx.library,
        version=ctx.version,
        items=[
            ContextItemOut(
                library=i.library, path=i.path, status=i.status, chain=_hops(i.chain), signature=i.signature,
                note=i.note, score=i.score,
            )
            for i in ctx.items
        ],
        prompt=ctx.to_prompt(),
        query_ms=ctx.query_ms,
        graph_queries=ctx.graph_queries,
        took_ms=took_ms,
    )


def timeline_out(tl: ApiTimeline, took_ms: float = 0.0) -> TimelineOut:
    return TimelineOut(
        library=tl.library,
        path=tl.path,
        kind=tl.kind,
        versions=[VersionFactOut(**asdict(v)) for v in tl.versions],
        replaced_by=_hops(tl.replaced_by),
        graph_queries=tl.graph_queries,
        took_ms=took_ms,
    )


def outcome_out(res: OutcomeResult, took_ms: float = 0.0) -> OutcomeOut:
    return OutcomeOut(
        episode_id=res.episode_id,
        used_context=res.used_context,
        maintain_scheduled=res.maintain_scheduled,
        learned=LearnReportOut(**asdict(res.report)),
        took_ms=took_ms,
    )


def libraries_out(libs: list[LibraryInfo], took_ms: float = 0.0) -> LibrariesOut:
    return LibrariesOut(
        libraries=[
            LibraryOut(
                name=li.name,
                import_names=li.import_names,
                versions=[VersionOut(version=v.version, ord=v.ord, released=v.released) for v in li.versions],
            )
            for li in libs
        ],
        took_ms=took_ms,
    )
