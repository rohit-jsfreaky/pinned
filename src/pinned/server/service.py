"""PinnedService: the one place that runs product operations. The HTTP API, the MCP tools and the CLI all call it, so
validation, version resolution, error mapping and learning hooks behave the same on every surface."""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from pinned.contract import (
    ADDED_IN,
    ALIAS_OF,
    API,
    CHANGED_IN,
    CUES,
    EPISODE,
    HAS_VERSION,
    LIBRARY,
    MEMBER_OF,
    NEXT,
    REMOVED_IN,
    REPLACED_BY,
    SHORTCUT,
    TERM,
    USED,
    VERSION,
    ApiTimeline,
    Context,
    Episode,
    Finding,
    Graph,
    LearnReport,
    Learner,
    Search,
)
from pinned.graph import count_edges, count_nodes, version_ord

from . import evidence
from .config import Settings
from .errors import BadInput, GraphUnavailable, NotFound, graph_errors

log = logging.getLogger("pinned.server")

LIVE_RUN_ID = "live"
LABELS = (LIBRARY, VERSION, API, TERM, EPISODE)
# (source label, relationship) for every edge type in the schema; count_edges anchors on the source label.
EDGE_TYPES = (
    (LIBRARY, HAS_VERSION), (VERSION, NEXT), (API, ADDED_IN), (API, REMOVED_IN), (API, CHANGED_IN),
    (API, REPLACED_BY), (API, ALIAS_OF), (API, MEMBER_OF), (TERM, CUES), (TERM, SHORTCUT), (EPISODE, USED),
)

_LIBRARY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
MAX_TASK = 4_000
MAX_ERROR_TEXT = 20_000
MAX_ERROR_TYPE = 100
MAX_PATH = 300
LIBRARY_TTL_S = 30.0
STATS_TTL_S = 15.0
CONTEXT_MEMORY = 1024


@dataclass
class VersionInfo:
    version: str
    ord: int
    released: str = ""


@dataclass
class LibraryInfo:
    name: str
    import_names: list[str] = field(default_factory=list)
    versions: list[VersionInfo] = field(default_factory=list)  # oldest -> newest


@dataclass
class OutcomeResult:
    episode_id: str
    report: LearnReport
    used_context: bool
    maintain_scheduled: bool


class PinnedService:
    def __init__(self, search: Search, learner: Learner, graph: Graph, settings: Settings | None = None):
        self.search = search
        self.learner = learner
        self.graph = graph
        self.settings = settings or Settings()
        self._lib_lock = threading.Lock()
        self._libs: dict[str, LibraryInfo] = {}
        self._libs_at = 0.0
        self._stats_lock = threading.Lock()
        self._stats: dict[str, Any] | None = None
        self._stats_at = 0.0
        self._learn_lock = threading.Lock()
        self._maintain_lock = threading.Lock()
        self._outcomes = 0
        self._contexts: OrderedDict[tuple[str, str, str], Context] = OrderedDict()
        self._ctx_lock = threading.Lock()

    @property
    def graph_url(self) -> str:
        return str(getattr(self.graph, "url", ""))

    # ------------------------------------------------------------------ validation
    def _code(self, code: str, *, required: bool = True) -> str:
        if not isinstance(code, str):
            raise BadInput("code must be a string")
        if required and not code.strip():
            raise BadInput("code is empty")
        size = len(code.encode("utf-8"))
        if size > self.settings.max_code_bytes:
            raise BadInput(
                f"code is {size} bytes; the limit is {self.settings.max_code_bytes}",
                {"limit_bytes": self.settings.max_code_bytes, "got_bytes": size},
            )
        return code

    def _k(self, k: int) -> int:
        if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= self.settings.max_k:
            raise BadInput(f"k must be an integer from 1 to {self.settings.max_k}", {"got": k})
        return k

    @staticmethod
    def _text(name: str, value: str, limit: int, *, required: bool = False) -> str:
        value = (value or "").strip()
        if required and not value:
            raise BadInput(f"{name} is empty")
        if len(value) > limit:
            raise BadInput(f"{name} is {len(value)} characters; the limit is {limit}", {"limit": limit})
        return value

    @staticmethod
    def _path(path: str) -> str:
        path = (path or "").strip()
        if not path or len(path) > MAX_PATH or not _PATH_RE.match(path):
            raise BadInput(
                f"path must be a dotted import path like pandas.DataFrame.append, got {path[:80]!r}",
                {"limit": MAX_PATH},
            )
        return path

    # ------------------------------------------------------------------ libraries / versions
    def _load_libraries(self) -> dict[str, LibraryInfo]:
        with graph_errors(self.graph_url):
            lib_rows = self.graph.query(f"MATCH (l:{LIBRARY}) RETURN l.name AS name, l.import_names AS import_names")
            ver_rows = self.graph.query(
                f"MATCH (v:{VERSION}) RETURN v.library AS library, v.version AS version, v.ord AS ord, "
                "v.released AS released"
            )
        libs: dict[str, LibraryInfo] = {}
        for r in lib_rows:
            if not r.get("name"):
                continue
            names = [p for p in str(r.get("import_names") or "").split(",") if p]
            libs[r["name"]] = LibraryInfo(name=r["name"], import_names=names)
        for r in ver_rows:
            lib = libs.get(r.get("library") or "")
            if lib is None or not r.get("version"):
                continue
            ord_ = r.get("ord")
            if not isinstance(ord_, int):
                try:
                    ord_ = version_ord(r["version"])
                except ValueError:
                    continue
            lib.versions.append(VersionInfo(r["version"], ord_, str(r.get("released") or "")))
        for lib in libs.values():
            lib.versions.sort(key=lambda v: v.ord)
        return libs

    def libraries(self, *, fresh: bool = False) -> dict[str, LibraryInfo]:
        with self._lib_lock:
            if fresh or not self._libs_at or time.monotonic() - self._libs_at > LIBRARY_TTL_S:
                self._libs = self._load_libraries()
                self._libs_at = time.monotonic()
            return self._libs

    def resolve(self, library: str, version: str | None) -> tuple[str, str]:
        """Normalise and check (library, version) against what the graph holds. `version=None` checks the library only.

        2.0 and 2.0.0 are the same version (compared by version_ord). Unknown -> NotFound listing what is known.
        """
        lib = (library or "").strip().lower()
        if not _LIBRARY_RE.match(lib):
            raise BadInput(f"library must be a PyPI project name like scikit-learn, got {library[:80]!r}")
        want: int | None = None
        if version is not None:
            v = (version or "").strip()
            try:
                want = version_ord(v)
            except ValueError as e:
                raise BadInput(f"version {v[:40]!r} is not a valid version ({e})") from None
        for attempt in (0, 1):
            libs = self.libraries(fresh=attempt == 1)
            info = libs.get(lib)
            if info is None:
                continue
            if want is None:
                return lib, ""
            for vi in info.versions:
                if vi.ord == want:
                    return lib, vi.version
        libs = self.libraries()
        info = libs.get(lib)
        if info is None:
            raise NotFound(
                f"{lib} is not in the Pinned graph yet",
                {"known_libraries": sorted(libs)[:100]},
            )
        lower = [v.version for v in info.versions if v.ord < (want or 0)]
        higher = [v.version for v in info.versions if v.ord > (want or 0)]
        raise NotFound(
            f"{lib} {version} is not in the Pinned graph yet",
            {
                "known_versions": [v.version for v in info.versions],
                "nearest_lower": lower[-1] if lower else None,
                "nearest_higher": higher[0] if higher else None,
            },
        )

    def list_libraries(self) -> list[LibraryInfo]:
        return sorted(self.libraries().values(), key=lambda li: li.name)

    # ------------------------------------------------------------------ the four agent operations
    def check(self, code: str, library: str, version: str) -> tuple[str, str, list[Finding]]:
        code = self._code(code)
        lib, ver = self.resolve(library, version)
        with graph_errors(self.graph_url):
            findings = self.search.check_calls(code, lib, ver)
        return lib, ver, findings

    def how(self, task: str, library: str, version: str, code: str = "", k: int = 8) -> Context:
        task = self._text("task", task, MAX_TASK, required=True)
        code = self._code(code or "", required=False)
        k = self._k(k)
        lib, ver = self.resolve(library, version)
        with graph_errors(self.graph_url):
            ctx = self.search.context_for(task, code, lib, ver, k=k)
        self._remember(lib, ver, task, ctx)
        return ctx

    def explain(self, library: str, path: str) -> ApiTimeline:
        path = self._path(path)
        lib, _ = self.resolve(library, None)
        with graph_errors(self.graph_url):
            tl = self.search.explain_api(lib, path)
        known = [v for v in tl.versions if v.status not in ("missing", "unknown")]
        if not known and not tl.replaced_by:
            raise NotFound(
                f"the Pinned graph has no facts about {path} in {lib}",
                {"library": lib, "path": path, "versions_checked": len(tl.versions)},
            )
        return tl

    def outcome(
        self,
        code: str,
        library: str,
        version: str,
        passed: bool,
        error_type: str = "",
        error_text: str = "",
        task: str = "",
    ) -> OutcomeResult:
        """Feed one real pass/fail of an agent's code to the learner (run_id "live"), with the context Pinned gave
        for the same task when it still remembers it (else a fresh one), so the learner knows which edges were used."""
        code = self._code(code)
        if not isinstance(passed, bool):
            raise BadInput("passed must be true or false")
        error_type = self._text("error_type", error_type, MAX_ERROR_TYPE)
        error_text = self._text("error_text", error_text, MAX_ERROR_TEXT)
        task = self._text("task", task, MAX_TASK)
        lib, ver = self.resolve(library, version)
        ctx = self._recall(lib, ver, task) if task else None
        with graph_errors(self.graph_url):
            if ctx is None and task:
                ctx = self.search.context_for(task, code, lib, ver)
            digest = hashlib.sha1(f"{lib}\x00{ver}\x00{task}\x00{code}".encode()).hexdigest()[:16]
            episode = Episode(
                run_id=LIVE_RUN_ID,
                problem_id=f"live-{digest}",
                iteration=0,
                library=lib,
                version=ver,
                problem=task,
                starting_code="",
                context=ctx,
                code=code,
                passed=passed,
                error_type=error_type,
                error_text=error_text,
            )
            with self._learn_lock:  # record() reads and rewrites edge weights: one at a time
                report = self.learner.record(episode)
                self._outcomes += 1
                due = self.settings.maintain_every > 0 and self._outcomes % self.settings.maintain_every == 0
        with self._stats_lock:
            self._stats = None
        scheduled = self._schedule_maintain() if due else False
        return OutcomeResult(
            episode_id=f"{LIVE_RUN_ID}|{episode.problem_id}|0",
            report=report,
            used_context=ctx is not None,
            maintain_scheduled=scheduled,
        )

    def _schedule_maintain(self) -> bool:
        """learner.maintain() (path compression, merge/split, decay) in the background; one run at a time."""
        if not self._maintain_lock.acquire(blocking=False):
            return False

        def run() -> None:
            try:
                with self._learn_lock:
                    report = self.learner.maintain()
                log.info("maintain done: %s", report)
            except Exception:
                log.exception("learner.maintain() failed")
            finally:
                self._maintain_lock.release()

        threading.Thread(target=run, name="pinned-maintain", daemon=True).start()
        return True

    # ------------------------------------------------------------------ context memory (how -> outcome)
    def _remember(self, lib: str, ver: str, task: str, ctx: Context) -> None:
        with self._ctx_lock:
            key = (lib, ver, task)
            self._contexts[key] = ctx
            self._contexts.move_to_end(key)
            while len(self._contexts) > CONTEXT_MEMORY:
                self._contexts.popitem(last=False)

    def _recall(self, lib: str, ver: str, task: str) -> Context | None:
        with self._ctx_lock:
            return self._contexts.get((lib, ver, task))

    # ------------------------------------------------------------------ stats / curve / health
    def stats(self) -> dict[str, Any]:
        with self._stats_lock:
            if self._stats is not None and time.monotonic() - self._stats_at < STATS_TTL_S:
                return self._stats
        t0 = time.perf_counter()
        with graph_errors(self.graph_url):
            nodes = {label: count_nodes(self.graph, label) for label in LABELS}  # type: ignore[arg-type]
            edges = {rel: count_edges(self.graph, src, rel) for src, rel in EDGE_TYPES}  # type: ignore[arg-type]
        out = {
            "graph": {
                "nodes": nodes,
                "edges": edges,
                "total_nodes": sum(nodes.values()),
                "total_edges": sum(edges.values()),
                "count_ms": round((time.perf_counter() - t0) * 1000, 1),
            },
            "latency": {
                "graph_layer": evidence.latency_summary(self.settings.evidence_dir, "load"),
                "search": evidence.latency_summary(self.settings.evidence_dir, "search"),
            },
        }
        with self._stats_lock:
            self._stats, self._stats_at = out, time.monotonic()
        return out

    def curve(self) -> dict[str, Any]:
        return evidence.curve(self.settings.evidence_dir)

    def health(self) -> dict[str, Any]:
        """Checks that HydraDB answers both its health probe and a real query. Raises GraphUnavailable if not."""
        t0 = time.perf_counter()
        ready = getattr(self.graph, "ready", None)
        if callable(ready) and not ready():
            raise GraphUnavailable(f"HydraDB is not answering /healthz at {self.graph_url or 'the configured URL'}")
        with graph_errors(self.graph_url):
            self.graph.query(f"MATCH (l:{LIBRARY}) RETURN count(*) AS n")
        return {"status": "ok", "hydradb": {"ok": True, "ms": round((time.perf_counter() - t0) * 1000, 1)}}
