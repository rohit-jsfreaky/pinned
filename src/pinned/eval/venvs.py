"""One virtualenv per (python_version, library==version, additional_dependencies), built like the official harness.

Follows mrcabbage972/GitChameleonBenchmark `gitchameleon/create_venvs.py` (commit 3a1b604):
- the same cache key (`generate_venv_cache_key`) and folder name `gcham_venv_<key>`, so venvs are interchangeable;
- the same interpreters (CPython 3.7.17 / 3.9.19 / 3.10.14);
- the same install order: `<library>==<version>` + additional deps, then the pinned pytest / pytest-cov / numpy / scipy
  for that Python unless the dependency list already names them (same substring rule as upstream).
Differences (on purpose, recorded in the progress file): `uv pip` instead of pip (hard-links from one cache, so 99 venvs
fit on a 30 GB disk, and it is much faster) and CPU-only torch wheels from download.pytorch.org.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .dataset import Problem

DEFAULT_VENV_DIR = Path("data/venvs")
DEFAULT_PYTHONS_DIR = Path("data/pythons")
NLTK_DATA_DIR = Path("data/nltk_data")
NLTK_PACKAGES = ("punkt", "wordnet", "omw-1.4")   # what the official Dockerfile downloads
MARKER = ".pinned_venv.json"

PYTHON_VERSIONS = {"3.7": "3.7.17", "3.9": "3.9.19", "3.10": "3.10.14"}

# Upstream's extra pins per Python version (create_venvs.py `install_packages`).
EXTRA_PINS: dict[str, dict[str, str]] = {
    "pytest": {"3.7": "pytest==6.2.5", "3.9": "pytest==7.1.2", "3.10": "pytest==7.2.0"},
    "pytest-cov": {"3.7": "pytest-cov==4.1.0", "3.9": "pytest-cov==4.1.0", "3.10": "pytest-cov==4.1.0"},
    "numpy": {"3.7": "numpy==1.21.6", "3.9": "numpy==1.21.6", "3.10": "numpy==1.23"},
    "scipy": {"3.7": "scipy==1.7.1", "3.9": "scipy==1.9.1", "3.10": "scipy==1.10.1"},
}
# Era constraints: a transitive dependency released long after the pinned library breaks it, so the ground-truth
# solution fails (found by the runner self-check). Each entry caps that dependency at what was current when the library
# version came out. Keyed by (library, version); applied to every install step with `uv pip --constraint`.
ERA_CONSTRAINTS: dict[tuple[str, str], tuple[str, ...]] = {
    # werkzeug 3.1 (2024-11) changed safe_join(base, None); flask 3.0.0 (2023-09) shipped with werkzeug 3.0.x
    ("flask", "3.0.0"): ("werkzeug<3.1",),
    # gradio_client >= 0.3 removed gradio_client.serializing, which gradio 3.24 / 3.36 import; gradio 3.24.0 (2023-03)
    # also needs the 0.0.x serializer API (0.1+ has no "serialized_input" key)
    ("gradio", "3.24.0"): ("gradio_client<0.1",),
    # starlette 1.x changed TemplateResponse(name, context) -> gradio 3.36.0 (2023-07) serves 500 on "/" and launch()
    # decides localhost is unreachable; fastapi 0.100.x (its era) pins starlette 0.27 and predates pydantic 2.1+
    ("gradio", "3.36.0"): ("gradio_client<0.3", "fastapi<0.101", "pydantic<2.1"),
    # joblib 0.12 renamed Memory.cachedir -> location; librosa 0.6.0 (2018-02) reads .cachedir
    # numba 0.49 removed numba.decorators, which librosa 0.6.x / 0.7.x import
    ("librosa", "0.6.0"): ("joblib<0.12", "numba<0.49"),
    ("librosa", "0.7.0"): ("numba<0.49",),
    ("librosa", "0.7.1"): ("numba<0.49",),
}
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
PYPI_INDEX = "https://pypi.org/simple"
INSTALL_TIMEOUT_S = 3600


def venv_key(python_version: str, library: str, version: str, extra_deps: str) -> str:
    """Identical to upstream `gitchameleon.utils.generate_venv_cache_key`."""
    key_string = "-".join([python_version, library, version, extra_deps])
    return hashlib.sha256(key_string.encode("utf-8")).hexdigest()[0:12]


@dataclass(frozen=True)
class VenvSpec:
    python_version: str
    library: str
    version: str
    additional_dependencies: str

    @classmethod
    def of(cls, p: Problem) -> "VenvSpec":
        return cls(p.python_version, p.library, p.version, p.additional_dependencies)

    @property
    def key(self) -> str:
        return venv_key(self.python_version, self.library, self.version, self.additional_dependencies)

    @property
    def dirname(self) -> str:
        return f"gcham_venv_{self.key}"

    def deps(self) -> list[str]:
        """Additional dependencies, minus upstream's ignored entries (`pip=...`, `-`)."""
        return [d for d in self.additional_dependencies.split() if d.strip() and d != "-" and not d.startswith("pip=")]

    def install_steps(self) -> list[list[str]]:
        """Requirement lists, one `uv pip install` each, in upstream's order."""
        deps = self.deps()
        steps = [[f"{self.library}=={self.version}", *deps]]
        deps_lower = [d.lower() for d in self.additional_dependencies.split()]
        lib = self.library.lower()
        extra = []
        if lib != "pytest":
            extra += ["pytest", "pytest-cov"]
        if lib != "numpy":
            extra.append("numpy")
        if lib != "scipy":
            extra.append("scipy")
        for name in extra:
            pin = EXTRA_PINS[name].get(self.python_version)
            if pin and all(name not in d for d in deps_lower):
                steps.append([pin])
        return steps

    def constraints(self) -> tuple[str, ...]:
        return ERA_CONSTRAINTS.get((self.library.lower(), self.version), ())

    def needs_torch_index(self) -> bool:
        reqs = [r.lower() for step in self.install_steps() for r in step]
        return any(r.split("==")[0] == "torch" for r in reqs)


def specs_for(problems: list[Problem]) -> dict[str, VenvSpec]:
    """Deduped venv specs keyed by venv key (99 for the full dataset)."""
    out: dict[str, VenvSpec] = {}
    for p in problems:
        s = VenvSpec.of(p)
        out.setdefault(s.key, s)
    return out


def venv_python(venv_dir: Path, spec: VenvSpec) -> Path:
    return venv_dir / spec.dirname / "bin" / "python"


def is_built(venv_dir: Path, spec: VenvSpec) -> bool:
    m = venv_dir / spec.dirname / MARKER
    if not m.exists() or not venv_python(venv_dir, spec).exists():
        return False
    try:
        meta = json.loads(m.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return meta.get("ok") is True and tuple(meta.get("constraints", ())) == spec.constraints()



# ---------------------------------------------------------------- interpreters
def find_interpreter(python_version: str, pythons_dir: Path = DEFAULT_PYTHONS_DIR) -> Path:
    """CPython at the exact upstream patch version. 3.9/3.10 come from `uv python`; 3.7.17 is built from source into
    `data/pythons/3.7.17` by scripts/cloud/build-python37.sh (uv has no 3.7). PINNED_PYTHON_<major>_<minor> overrides."""
    env = os.environ.get("PINNED_PYTHON_" + python_version.replace(".", "_"))
    if env:
        return Path(env)
    full = PYTHON_VERSIONS.get(python_version)
    if full is None:
        raise ValueError(f"unsupported python_version {python_version!r}")
    local = pythons_dir / full / "bin" / f"python{python_version}"
    if local.exists():
        return local.resolve()
    res = subprocess.run(["uv", "python", "find", full], capture_output=True, text=True)
    if res.returncode == 0 and res.stdout.strip():
        return Path(res.stdout.strip())
    raise FileNotFoundError(
        f"no CPython {full}: run `uv python install {full}`" if python_version != "3.7"
        else f"no CPython {full}: run scripts/cloud/build-python37.sh"
    )


# ---------------------------------------------------------------- build
@dataclass
class BuildResult:
    key: str
    spec: dict
    ok: bool
    seconds: float = 0.0
    bytes: int = 0                     # apparent size of the venv (hard-linked files counted in full)
    skipped: bool = False              # already built (resume)
    error: str = ""
    packages: dict[str, str] = field(default_factory=dict)


def _du_bytes(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def _run(cmd: list[str], timeout: int = INSTALL_TIMEOUT_S) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _freeze(py: Path) -> dict[str, str]:
    res = _run(["uv", "pip", "freeze", "--python", str(py)], timeout=120)
    pkgs: dict[str, str] = {}
    for line in res.stdout.splitlines():
        if "==" in line:
            name, ver = line.split("==", 1)
            pkgs[name.strip().lower()] = ver.strip()
    return pkgs


def build_one(spec: VenvSpec, venv_dir: Path = DEFAULT_VENV_DIR, pythons_dir: Path = DEFAULT_PYTHONS_DIR,
              rebuild: bool = False) -> BuildResult:
    path = venv_dir / spec.dirname
    if not rebuild and is_built(venv_dir, spec):
        meta = json.loads((path / MARKER).read_text())
        return BuildResult(spec.key, asdict(spec), True, meta.get("seconds", 0.0), meta.get("bytes", 0), True,
                           packages=meta.get("packages", {}))
    t0 = time.monotonic()
    if path.exists():
        shutil.rmtree(path)
    try:
        interp = find_interpreter(spec.python_version, pythons_dir)
        # stdlib venv like upstream, but symlinked: `--copies` breaks uv's relocatable CPython builds (the copied
        # binary loses its $ORIGIN-relative libpython and exits 127).
        res = _run([str(interp), "-m", "venv", str(path)], timeout=600)
        if res.returncode != 0:
            raise RuntimeError(f"venv creation failed: {res.stderr[-2000:]}")
        py = path / "bin" / "python"
        index_args = (["--index-url", TORCH_CPU_INDEX, "--extra-index-url", PYPI_INDEX,
                       "--index-strategy", "unsafe-best-match"] if spec.needs_torch_index() else [])
        constraint_args: list[str] = []
        if spec.constraints():
            cfile = path / "era-constraints.txt"
            cfile.write_text("\n".join(spec.constraints()) + "\n")
            constraint_args = ["--constraint", str(cfile)]
        for reqs in spec.install_steps():
            res = _run(["uv", "pip", "install", "--python", str(py), *index_args, *constraint_args, *reqs])
            if res.returncode != 0:
                raise RuntimeError(f"install {' '.join(reqs)} failed:\n{res.stderr[-3000:]}")
        packages = _freeze(py)
        if "nltk" in packages:
            _ensure_nltk_data(py)
        seconds = round(time.monotonic() - t0, 1)
        size = _du_bytes(path)
        (path / MARKER).write_text(json.dumps(
            {"ok": True, "spec": asdict(spec), "constraints": list(spec.constraints()), "seconds": seconds, "bytes": size, "packages": packages}, indent=1))
        return BuildResult(spec.key, asdict(spec), True, seconds, size, packages=packages)
    except (RuntimeError, OSError, subprocess.TimeoutExpired, FileNotFoundError, ValueError) as e:
        shutil.rmtree(path, ignore_errors=True)
        return BuildResult(spec.key, asdict(spec), False, round(time.monotonic() - t0, 1), error=str(e)[-3000:])


def _ensure_nltk_data(py: Path) -> None:
    """The official image pre-downloads punkt / wordnet / omw-1.4; tests that tokenize need them (offline)."""
    target = NLTK_DATA_DIR.resolve()
    if all((target / sub).exists() for sub in ("tokenizers/punkt", "corpora/wordnet.zip", "corpora/omw-1.4.zip")):
        return
    target.mkdir(parents=True, exist_ok=True)
    code = "import nltk,sys\nfor p in sys.argv[2:]:\n    assert nltk.download(p, download_dir=sys.argv[1], quiet=True), p\n"
    res = _run([str(py), "-c", code, str(target), *NLTK_PACKAGES], timeout=600)
    if res.returncode != 0:
        raise RuntimeError(f"nltk data download failed: {res.stderr[-1500:]}")


def build_all(specs: list[VenvSpec], venv_dir: Path = DEFAULT_VENV_DIR, pythons_dir: Path = DEFAULT_PYTHONS_DIR,
              workers: int = 4, rebuild: bool = False, log=print) -> list[BuildResult]:
    venv_dir.mkdir(parents=True, exist_ok=True)
    results: list[BuildResult] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(build_one, s, venv_dir, pythons_dir, rebuild): s for s in specs}
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            results.append(r)
            s = futs[fut]
            state = "skip" if r.skipped else ("ok" if r.ok else "FAIL")
            log(f"[{i}/{len(specs)}] {state} {s.dirname} py{s.python_version} {s.library}=={s.version} "
                f"{s.additional_dependencies} ({r.seconds}s)" + ("" if r.ok else f"\n  {r.error.splitlines()[-1:]}"))
    return sorted(results, key=lambda r: r.key)


def total_disk_bytes(venv_dir: Path) -> int:
    """Real disk use of all venvs (hard links counted once), via `du -s`."""
    res = _run(["du", "-sb", str(venv_dir)], timeout=600)
    return int(res.stdout.split()[0]) if res.returncode == 0 and res.stdout else 0
