"""PyPI: release lists, file choice, download + extraction of the Python sources of one release.

Only `.py` / `.pyi` files are kept; the archive is deleted right after extraction (torch wheels are GBs).
The names of compiled extension modules in the archive are recorded, because static loading cannot see inside them.
"""

from __future__ import annotations

import json
import re
import shutil
import tarfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import httpx
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from pinned.graph import version_ord

PYPI = "https://pypi.org/pypi/{name}/json"
_EXT = re.compile(r"\.(?P<tag>cpython-[^.]+|abi3|cp\d+[^.]*)?\.?(so|pyd)$")
_SKIP_TOP = {"tests", "test", "testing_utils", "docs", "doc", "examples", "benchmarks", "setup", "conftest", "build",
             "tools", "scripts"}


class PyPIError(RuntimeError):
    pass


@dataclass
class Release:
    version: str
    released: str                 # YYYY-MM-DD of the first upload
    files: list[dict] = field(default_factory=list)
    yanked: bool = False          # every file yanked: kept only so an exact pin from the dataset can still be loaded


@dataclass
class Extracted:
    """Sources of one release on disk."""

    root: Path                    # directory that holds the top-level packages / modules
    file: str                     # archive file name
    kind: str                     # pure-wheel | binary-wheel | sdist
    top_level: list[str]          # every importable top-level name (packages and modules, private ones too)
    compiled: list[str]           # dotted names of compiled extension modules found in the archive

    def manifest(self) -> dict:
        return {"file": self.file, "kind": self.kind, "top_level": self.top_level, "compiled": self.compiled}


class PyPIClient:
    def __init__(self, cache_dir: Path, timeout: float = 120.0, retries: int = 4):
        self.cache_dir = Path(cache_dir)
        self.retries = retries
        self._http = httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": "pinned-ingest"})

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------ metadata
    def _get(self, url: str) -> httpx.Response:
        delay = 1.0
        for attempt in range(self.retries + 1):
            try:
                r = self._http.get(url)
                if r.status_code == 404:
                    raise PyPIError(f"not found on PyPI: {url}")
                if r.status_code < 500 and r.status_code != 429:
                    r.raise_for_status()
                    return r
            except httpx.TransportError:
                if attempt == self.retries:
                    raise
            if attempt == self.retries:
                raise PyPIError(f"PyPI kept failing: {url}")
            time.sleep(delay)
            delay *= 2
        raise PyPIError(f"PyPI kept failing: {url}")

    def project(self, name: str, refresh: bool = False) -> dict:
        """The PyPI JSON document of a project (cached on disk; old releases never change)."""
        path = self.cache_dir / "meta" / f"{canonicalize_name(name)}.json"
        if path.exists() and not refresh:
            return json.loads(path.read_text())
        data = self._get(PYPI.format(name=canonicalize_name(name))).json()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return data

    def releases(self, name: str) -> list[Release]:
        """Final releases only (no pre / dev / post), oldest first. Fully yanked releases are flagged `yanked`."""
        out = []
        for ver, files in self.project(name)["releases"].items():
            try:
                v = Version(ver)
                version_ord(ver)
            except (InvalidVersion, ValueError):
                continue
            if v.is_prerelease or v.is_devrelease or v.is_postrelease or v.local:
                continue
            if not files:
                continue
            live = [f for f in files if not f.get("yanked")]
            day = min(f["upload_time_iso_8601"] for f in files)[:10]
            out.append(Release(ver, day, live or files, yanked=not live))
        return sorted(out, key=lambda r: Version(r.version))

    # ------------------------------------------------------------------ files
    @staticmethod
    def choose_file(release: Release, python: str = "") -> dict:
        """Pure-python wheel > manylinux x86_64 CPython wheel (matching `python` first) > sdist."""
        wheels = [f for f in release.files if f["filename"].endswith(".whl")]
        pure = [f for f in wheels if f["filename"].endswith("-none-any.whl")]
        if pure:
            return sorted(pure, key=lambda f: ("py3" not in f["filename"], f["filename"]))[0]
        tag = "cp" + python.replace(".", "") if python else ""

        def linux(f: dict) -> bool:
            n = f["filename"]
            return "x86_64" in n and ("manylinux" in n or "linux_x86_64" in n) and ("-cp" in n or "-none-" in n)

        binary = [f for f in wheels if linux(f)]
        if binary:
            def rank(f: dict) -> tuple:
                n = f["filename"]
                cp = re.search(r"-cp(\d)(\d+)-", n)
                cpv = (int(cp.group(1)), int(cp.group(2))) if cp else (0, 0)
                return (f"-{tag}-" not in n if tag else True, "abi3" in n, tuple(-x for x in cpv), n)
            return sorted(binary, key=rank)[0]
        sdists = [f for f in release.files if f["packagetype"] == "sdist"]
        if sdists:
            return sorted(sdists, key=lambda f: (not f["filename"].endswith(".tar.gz"), f["filename"]))[0]
        raise PyPIError(f"no usable file for {release.version}: {[f['filename'] for f in release.files]}")

    def fetch_sources(self, library: str, release: Release, python: str = "") -> Extracted:
        """Download, extract .py/.pyi into <cache>/src/<lib>/<version>/, delete the archive. Cached by manifest."""
        dest = self.cache_dir / "src" / canonicalize_name(library) / release.version
        manifest = dest / "_pinned_manifest.json"
        if manifest.exists():
            m = json.loads(manifest.read_text())
            return Extracted(dest / m.get("subdir", ""), m["file"], m["kind"], m["top_level"], m["compiled"])
        f = self.choose_file(release, python)
        archive = self.cache_dir / "dl" / f["filename"]
        archive.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        try:
            self._download(f["url"], archive)
            if f["filename"].endswith(".whl"):
                ex = _extract_wheel(archive, dest)
                ex.kind = "pure-wheel" if f["filename"].endswith("-none-any.whl") else "binary-wheel"
                subdir = ""
            else:
                ex, subdir = _extract_sdist(archive, dest)
        except BaseException:
            shutil.rmtree(dest, ignore_errors=True)
            raise
        finally:
            archive.unlink(missing_ok=True)
        manifest.write_text(json.dumps({**ex.manifest(), "subdir": subdir}))
        return ex

    def _download(self, url: str, out: Path) -> None:
        delay = 2.0
        for attempt in range(self.retries + 1):
            try:
                with self._http.stream("GET", url) as r:
                    r.raise_for_status()
                    with out.open("wb") as fh:
                        for chunk in r.iter_bytes(1 << 20):
                            fh.write(chunk)
                return
            except (httpx.TransportError, httpx.HTTPStatusError):
                if attempt == self.retries:
                    raise
                time.sleep(delay)
                delay *= 2


# ---------------------------------------------------------------------------------------------------- extraction
def _safe(name: str) -> PurePosixPath | None:
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        return None
    return p


def _module_name(rel: PurePosixPath, z: zipfile.ZipFile | None = None, info: zipfile.ZipInfo | None = None) -> str | None:
    """Dotted module name of a Python extension module file, or None.

    A tagged name (`_ufuncs.cpython-310-x86_64-linux-gnu.so`, `.abi3.so`) is an extension module. A plain `x.so` is one
    only if it exports `PyInit_x` (lightgbm's `lib_lightgbm.so` is a ctypes library, not a module)."""
    m = _EXT.search(rel.name)
    if not m:
        return None
    stem = rel.name.split(".")[0]
    if not all(p.isidentifier() for p in list(rel.parts[:-1]) + [stem]):
        return None  # e.g. scipy.libs/libopenblas-r0.so (vendored shared libraries)
    if not m.group("tag") and not (z is not None and info is not None and _exports_pyinit(z, info, stem)):
        return None
    return ".".join(list(rel.parts[:-1]) + [stem])


def _exports_pyinit(z: zipfile.ZipFile, info: zipfile.ZipInfo, stem: str) -> bool:
    needle = f"PyInit_{stem}".encode()
    tail = b""
    with z.open(info) as fh:
        while chunk := fh.read(1 << 20):
            if needle in tail + chunk:
                return True
            tail = chunk[-len(needle):]
    return False


def _extract_wheel(archive: Path, dest: Path) -> Extracted:
    compiled: list[str] = []
    tops: set[str] = set()
    top_txt: list[str] = []
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            rel = _safe(info.filename)
            if rel is None or info.is_dir():
                continue
            first = rel.parts[0]
            if first.endswith(".dist-info"):
                if rel.name == "top_level.txt":
                    top_txt = [x.strip() for x in z.read(info).decode().splitlines() if x.strip()]
                continue
            if first.endswith(".data"):
                # purelib/platlib payloads live under <name>.data/(purelib|platlib)/...
                if len(rel.parts) > 2 and rel.parts[1] in ("purelib", "platlib"):
                    rel = PurePosixPath(*rel.parts[2:])
                else:
                    continue
            if _EXT.search(rel.name):
                mod = _module_name(rel, z, info)
                if mod:
                    compiled.append(mod)
                    tops.add(rel.parts[0] if len(rel.parts) > 1 else mod)
                continue
            if rel.suffix not in (".py", ".pyi"):
                continue
            tops.add(rel.parts[0] if len(rel.parts) > 1 else rel.name.rsplit(".", 1)[0])
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(z.read(info))
    top = sorted(set(t.replace("/", ".") for t in top_txt) | tops)
    top = [t for t in top if t.isidentifier()]
    return Extracted(dest, archive.name, "wheel", top, sorted(set(compiled)))


def _extract_sdist(archive: Path, dest: Path) -> tuple[Extracted, str]:
    members: list[tuple[PurePosixPath, bytes]] = []
    top_txt: list[str] = []
    pyx: list[PurePosixPath] = []
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            items = [(i.filename, z.read(i)) for i in z.infolist()
                     if not i.is_dir() and i.filename.endswith((".py", ".pyi", "top_level.txt", ".pyx"))]
    else:
        with tarfile.open(archive) as t:
            items = []
            for m in t.getmembers():
                if m.isfile() and m.name.endswith((".py", ".pyi", "top_level.txt", ".pyx")):
                    fh = t.extractfile(m)
                    if fh is not None:
                        items.append((m.name, fh.read()))
    for name, data in items:
        rel = _safe(name)
        if rel is None or len(rel.parts) < 2:
            continue
        rel = PurePosixPath(*rel.parts[1:])  # strip "<project>-<version>/"
        if rel.name == "top_level.txt" and rel.parts[0].endswith(".egg-info"):
            top_txt = [x.strip() for x in data.decode().splitlines() if x.strip()]
        elif rel.suffix == ".pyx":
            pyx.append(rel)
        else:
            members.append((rel, data))
    use_src = any(r.parts[0] == "src" and len(r.parts) > 2 and r.name == "__init__.py" for r, _ in members)
    subdir = "src" if use_src else ""
    tops: set[str] = set()
    for rel, data in members:
        if use_src:
            if rel.parts[0] != "src":
                continue
            rel = PurePosixPath(*rel.parts[1:])
        out = dest / subdir / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        if len(rel.parts) == 1:
            tops.add(rel.name.rsplit(".", 1)[0])
        elif (dest / subdir / rel.parts[0] / "__init__.py").exists() or rel.parts[1] == "__init__.py":
            tops.add(rel.parts[0])
    root = dest / subdir
    tops = {t for t in tops if t.isidentifier() and t not in _SKIP_TOP
            and ((root / t / "__init__.py").exists() or (root / f"{t}.py").exists())}
    top = sorted(set(top_txt) | tops) if top_txt else sorted(tops)
    compiled = []
    for rel in pyx:
        r = PurePosixPath(*rel.parts[1:]) if use_src and rel.parts[0] == "src" else rel
        compiled.append(".".join(list(r.parts[:-1]) + [r.stem]))
    return Extracted(root, archive.name, "sdist", [t for t in top if t.isidentifier()], sorted(compiled)), subdir


def public_import_names(top_level: list[str]) -> list[str]:
    return [t for t in top_level if not t.startswith("_") and t not in _SKIP_TOP]

