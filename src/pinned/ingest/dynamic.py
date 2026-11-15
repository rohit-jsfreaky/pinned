"""Dynamic inspection of compiled modules, inside a throwaway `uv` venv for one exact release.

Static loading cannot see inside compiled extension modules (scipy.special ufuncs, numpy core, torch._C functions).
For the public modules that import from one, we install that exact release (plus the dataset's extra dependencies)
into a fresh venv on the dataset's Python, run `inspect_worker.py` there (griffe's dynamic inspection), read its
records and delete the venv. Nothing from the library is ever imported into this process.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .model import ApiRecord
from .static import render_params

log = logging.getLogger(__name__)

WORKER = Path(__file__).with_name("inspect_worker.py")
# uv dropped CPython 3.7 from its download list; the last uv line that still ships it installs it once.
LEGACY_UV = "uv@0.5.31"
# Extra package indexes per library: torch's CPU builds (the PyPI wheels pull GBs of CUDA libraries).
EXTRA_INDEX = {"torch": "https://download.pytorch.org/whl/cpu", "kymatio": "https://download.pytorch.org/whl/cpu"}
INSTALL_TIMEOUT = 1800
INSPECT_TIMEOUT = 1800


class DynamicError(RuntimeError):
    pass


@dataclass
class DynamicResult:
    records: list[ApiRecord] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)
    python: str = ""
    install: str = ""            # the install command line that worked


def _run(cmd: list[str], timeout: int, cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONWARNINGS": "ignore", "MPLBACKEND": "Agg", "UV_NO_PROGRESS": "1"}
    env.pop("VIRTUAL_ENV", None)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)


def find_python(version: str) -> str:
    """Path of a CPython `version` interpreter that uv manages, installing it when missing."""
    found = _run(["uv", "python", "find", "--managed-python", version], 120)
    if found.returncode == 0 and found.stdout.strip():
        return found.stdout.strip()
    inst = _run(["uv", "python", "install", version], 900)
    if inst.returncode != 0:
        inst = _run(["uvx", LEGACY_UV, "python", "install", version], 900)
    found = _run(["uv", "python", "find", "--managed-python", version], 120)
    if found.returncode != 0 or not found.stdout.strip():
        raise DynamicError(f"cannot get Python {version}: {inst.stderr.strip()[-300:]}")
    return found.stdout.strip()


def _cp_tags(files: list[dict]) -> list[str]:
    """Python versions with a CPython manylinux x86_64 wheel among a release's files ("3.10", "3.9", ...)."""
    tags = set()
    for f in files:
        n = f["filename"]
        if n.endswith(".whl") and "x86_64" in n and "linux" in n:
            for m in re.finditer(r"-cp(\d)(\d+)", n):
                tags.add(f"{m.group(1)}.{m.group(2)}")
    return sorted(tags, key=lambda t: tuple(int(x) for x in t.split(".")))


def python_candidates(preferred: str, files: list[dict]) -> list[str]:
    """The dataset's Python first, then Pythons this release ships wheels for (closest first)."""
    tags = _cp_tags(files)
    abi3 = any("abi3" in f["filename"] for f in files)
    cands = [preferred] if preferred else []
    if tags and preferred and preferred not in tags and not abi3:
        def dist(t: str) -> int:
            a = [int(x) for x in t.split(".")]
            b = [int(x) for x in preferred.split(".")]
            return abs((a[0] * 100 + a[1]) - (b[0] * 100 + b[1]))
        cands += sorted((t for t in tags if t != preferred), key=dist)
    if preferred and not tags:
        # a pure-Python (or sdist-only) release may predate the dataset's Python: older ones are the last resort
        major, minor = (int(x) for x in preferred.split("."))
        cands += [f"{major}.{m}" for m in range(minor - 1, 6, -1)]
    cands = [c for c in cands if tuple(int(x) for x in c.split(".")) >= (3, 7)]
    return list(dict.fromkeys(cands)) or ["3.10"]


def _requirements(dep_sets: list[str]) -> list[str]:
    """The dataset's richest dependency set for this pair ("numpy==1.21.6 scipy==1.7.3" -> two pins)."""
    best = max(dep_sets, key=lambda s: len(s.split()), default="")
    return best.split()


def install_attempts(library: str, pypi_version: str, released: str, dep_sets: list[str]) -> list[list[str]]:
    """uv pip install argument lists, most faithful first: the dataset's pins resolved as of the release date, the
    pins alone, everything as of the release date, then plain (newest compatible dependencies)."""
    # setuptools<70 keeps `pkg_resources`, which many older releases import at module level (spacy 3.4, ...)
    base = [f"{library}=={pypi_version}", "griffe", "setuptools<70"]
    index = ["--index", EXTRA_INDEX[library]] if library in EXTRA_INDEX else []
    deps = _requirements(dep_sets)
    cutoff = (date.fromisoformat(released) + timedelta(days=120)).isoformat()
    # resolve dependencies as of the release (avoids e.g. numpy 2 under an old scipy); griffe/setuptools are exempt
    as_of = ["--exclude-newer", cutoff, "--exclude-newer-package", "griffe=false",
             "--exclude-newer-package", "setuptools=false"]
    attempts = []
    if index:
        # the extra index has no upload dates (no cutoff), and uv takes a package from the FIRST index that has it:
        # that index carries its own setuptools, so the setuptools pin (only needed for pkg_resources) is left out
        base = base[:2]
        attempts += [index + base + deps] if deps else []
        attempts.append(index + base)
        return attempts
    if deps:
        # the dataset's pins stay exact (exempt from the cutoff); everything else resolves as of the release date
        pinned = [x for d in deps for x in ("--exclude-newer-package", f"{re.split(r'[=<>!~ ]', d)[0]}=false")]
        attempts += [as_of + pinned + base + deps, base + deps]
    attempts += [as_of + base, base]
    return attempts


class VenvInspector:
    def __init__(self, work_dir: Path):
        self.work_dir = Path(work_dir)

    def inspect(self, library: str, pypi_version: str, released: str, python: str, files: list[dict],
                dep_sets: list[str], modules: list[str], owners: list[str]) -> DynamicResult:
        if not modules:
            return DynamicResult()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        failures: list[str] = []
        # pass 1: wheels only, on every candidate Python (a source build may silently drop compiled modules);
        # pass 2: allow building from source
        for binary_only in (True, False):
            for py in python_candidates(python, files):
                res = self._try(library, pypi_version, released, py, dep_sets, modules, owners, binary_only, failures)
                if res is not None:
                    return res
        raise DynamicError(f"{library} {pypi_version}: dynamic inspection failed: " + " | ".join(failures)[-1500:])

    def _try(self, library: str, pypi_version: str, released: str, py: str, dep_sets: list[str], modules: list[str],
             owners: list[str], binary_only: bool, failures: list[str]) -> DynamicResult | None:
        try:
            interp = find_python(py)
        except DynamicError as e:
            failures.append(str(e))
            return None
        tmp = Path(tempfile.mkdtemp(prefix=f"{library}-{pypi_version}-py{py}-", dir=self.work_dir))
        try:
            for i, args in enumerate(install_attempts(library, pypi_version, released, dep_sets)):
                # a fresh venv per attempt: uv keeps installed packages that already satisfy a later attempt
                venv = tmp / f"venv{i}"
                shutil.rmtree(tmp / f"venv{i - 1}", ignore_errors=True)
                r = _run(["uv", "venv", "-q", "-p", interp, str(venv)], 300)
                if r.returncode != 0:
                    failures.append(f"py{py} venv: {r.stderr.strip()[-200:]}")
                    return None
                vpy = venv / "bin" / "python"
                # --no-cache: one venv per release would otherwise fill the disk with uv's wheel cache (torch: GBs)
                full = ["uv", "pip", "install", "-q", "--no-cache", "--python", str(vpy)]
                full += (["--only-binary", ":all:"] if binary_only else []) + args
                r = _run(full, INSTALL_TIMEOUT)
                if r.returncode != 0:
                    failures.append(f"py{py} install {' '.join(args)}: {r.stderr.strip()[-300:]}")
                    continue
                try:
                    res = self.run_worker(str(vpy), modules, owners, tmp)
                except DynamicError as e:
                    failures.append(f"py{py} worker: {e}")
                    continue
                if len(res.errors) == len(modules):  # nothing importable with these dependencies: next attempt
                    failures.append(f"py{py} {' '.join(args)}: every module failed to import, e.g. "
                                    f"{next(iter(res.errors.values()))}")
                    continue
                res.install = " ".join(full[3:])
                return res
            return None
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @staticmethod
    def run_worker(python: str, modules: list[str], owners: list[str], work: Path,
                   extra_path: list[str] = ()) -> DynamicResult:
        """Run inspect_worker.py with `python` in isolated mode. `extra_path` = directories put first on the
        worker's sys.path (the venv's site-packages already are)."""
        req, out = work / "request.json", work / "result.json"
        req.write_text(json.dumps({"modules": modules, "own": owners, "path": list(extra_path)}))
        r = _run([python, "-I", str(WORKER), str(req), str(out)], INSPECT_TIMEOUT, cwd=work)
        if r.returncode != 0 or not out.exists():
            raise DynamicError(r.stderr.strip()[-400:] or f"worker exit {r.returncode}")
        data = json.loads(out.read_text())
        return DynamicResult(records=[_record(d) for d in data["records"]], errors=data.get("errors", {}),
                             notes=data.get("notes", {}), python=data.get("python", ""))


def _record(d: dict) -> ApiRecord:
    sig = d.get("sig", "")
    if "params" in d:
        sig = render_params([tuple(p) for p in d["params"]], bool(d.get("drop_first")))
    return ApiRecord(path=d["path"], kind=d["kind"], sig=sig, doc=d.get("doc", ""), target=d.get("target", ""),
                     origin="dynamic")
