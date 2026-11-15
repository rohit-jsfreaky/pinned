"""Run one candidate solution against its problem's hidden test, in that problem's venv.

Same layout as upstream `gitchameleon/eval_sample.py`: a temp dir with `sample_<id>.py` (the candidate) and
`test_sample.py` (the hidden test), `python -m pytest` on it with a timeout; exit code 0 = pass.
On top of upstream we load a tiny pytest plugin (`_plugin/_pinned_report.py`) to get the exception type and frames, and
return `error_text` built ONLY from frames in the solution file plus a scrubbed message: no hidden-test source line,
no assertion detail from the test, no temp paths. That text is what the learner may see.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

TIMEOUT_S = 240            # upstream gitchameleon/constants.py TIMEOUT_SEC
MAX_MESSAGE = 1200         # chars kept from an exception message
MAX_FRAMES = 6             # solution frames kept (innermost last)
MIN_LEAK_LINE = 8          # a hidden-test line this long or longer must never appear in error_text
PLUGIN_DIR = Path(__file__).resolve().parent / "_plugin"
SOLUTION_NAME = "solution.py"   # how the solution file is named in error_text
TEST_FAILURE_TYPES = {"AssertionError", "Failed"}   # failures the hidden test raises itself (pytest.fail / assert)
WITHHELD = "[withheld: hidden test content]"


@dataclass(frozen=True)
class RunResult:
    passed: bool
    error_type: str = ""
    error_text: str = ""
    seconds: float = 0.0
    exit_code: int | None = None


def sample_filename(problem_id: str) -> str:
    return f"sample_{problem_id}.py"


def _test_lines(test_source: str) -> list[str]:
    lines = {ln.strip() for ln in test_source.splitlines()}
    return sorted((ln for ln in lines if len(ln) >= MIN_LEAK_LINE), key=len, reverse=True)


def scrub(text: str, test_source: str, tmpdir: str = "") -> str:
    """Remove temp paths and every line that contains a hidden-test source line."""
    if tmpdir:
        text = text.replace(tmpdir + os.sep, "").replace(tmpdir, "")
    leaks = _test_lines(test_source)
    out = []
    for line in text.splitlines():
        out.append(WITHHELD if any(t in line for t in leaks) else line)
    # collapse runs of withheld lines
    collapsed: list[str] = []
    for line in out:
        if line == WITHHELD and collapsed and collapsed[-1] == WITHHELD:
            continue
        collapsed.append(line)
    return "\n".join(collapsed)


def _primary_event(events: list[dict]) -> dict | None:
    failures = [e for e in events if not e.get("passed")]
    if not failures:
        return None
    collect = [e for e in failures if e.get("when") == "collect"]
    return (collect or failures)[0]


def format_error(event: dict, problem_id: str, test_source: str, tmpdir: str) -> tuple[str, str]:
    """(error_type, error_text) from one plugin event; frames from the solution file only."""
    etype = str(event.get("type") or "Error")
    sol = sample_filename(problem_id)
    frames = [f for f in event.get("frames", []) if os.path.basename(str(f.get("file", ""))) == sol]
    lines: list[str] = []
    syntax = event.get("syntax")
    if syntax and os.path.basename(str(syntax.get("file", ""))) == sol:
        lines.append(f'  File "{SOLUTION_NAME}", line {syntax.get("line", 0)}')
        if syntax.get("text"):
            lines.append(f"    {str(syntax['text']).strip()}")
    for f in frames[-MAX_FRAMES:]:
        lines.append(f'  File "{SOLUTION_NAME}", line {f.get("line")}, in {f.get("name")}')
        if f.get("code"):
            lines.append(f"    {str(f['code']).strip()}")
    if etype in TEST_FAILURE_TYPES and not frames:
        message = "the hidden test's check failed (details withheld)"
    else:
        message = str(event.get("message") or "")
        if len(message) > MAX_MESSAGE:
            message = message[:MAX_MESSAGE] + " ..."
    body = ("Traceback (solution frames only):\n" + "\n".join(lines) + "\n") if lines else ""
    # scrub first (test lines name `sample_<id>`), then hide the module name and temp paths
    text = scrub(f"{body}{etype}: {message}".rstrip(), test_source, tmpdir)
    text = text.replace(sol, SOLUTION_NAME).replace(sol[:-3], SOLUTION_NAME[:-3])
    return etype, text


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


def run_candidate(problem_id: str, code: str, python: Path, test_path: Path, timeout: float = TIMEOUT_S,
                  extra_env: dict[str, str] | None = None) -> RunResult:
    """Run `code` as sample_<id>.py against the hidden test at `test_path` with `python` (a venv interpreter)."""
    python = Path(os.path.abspath(python))   # absolute but NOT resolved: resolving would leave the venv
    if not python.exists():
        raise FileNotFoundError(f"venv interpreter missing: {python} (build venvs first)")
    test_source = Path(test_path).read_text()
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="pinned-run-") as tmp:
        work = Path(tmp) / "case"
        work.mkdir()
        (work / sample_filename(problem_id)).write_text(code)
        (work / "test_sample.py").write_text(test_source)
        report = Path(tmp) / "report.json"
        env = dict(os.environ)
        env.update({
            "PYTHONPATH": str(PLUGIN_DIR),
            "PINNED_REPORT": str(report),
            "PYTHONDONTWRITEBYTECODE": "1",
            "OMP_NUM_THREADS": "1",           # as in the upstream image
            "MPLBACKEND": "Agg",
            "HOME": tmp,                      # caches (matplotlib, numba, ...) go to the throwaway dir
            "NUMBA_CACHE_DIR": str(Path(tmp) / "numba"),
        })
        env.pop("PYTHONHOME", None)
        env.pop("VIRTUAL_ENV", None)
        if extra_env:
            env.update(extra_env)
        cmd = [str(python), "-m", "pytest", "--disable-warnings", "-q", "--tb=no", "--assert=plain",
               "-p", "no:cacheprovider", "-p", "_pinned_report", str(work)]
        proc = subprocess.Popen(cmd, cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                start_new_session=True)
        try:
            _out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            proc.communicate()
            return RunResult(False, "Timeout", f"Timeout: no result within {timeout:g}s",
                             round(time.monotonic() - t0, 2), None)
        seconds = round(time.monotonic() - t0, 2)
        rc = proc.returncode
        data = None
        if report.exists():
            try:
                data = json.loads(report.read_text())
            except json.JSONDecodeError:
                data = None
        if data is None:
            last = next((ln for ln in reversed(err.strip().splitlines()) if ln.strip()), "")
            text = scrub(f"process exited with code {rc} before pytest finished. {last}".strip(), test_source, str(work))
            return RunResult(False, "Crash", text, seconds, rc)
        events = data.get("events", [])
        ev = _primary_event(events)
        if rc == 0 and ev is None:
            return RunResult(True, "", "", seconds, rc)
        if ev is not None:
            etype, text = format_error(ev, problem_id, test_source, str(work))
            return RunResult(False, etype, text, seconds, rc)
        if rc == 5:
            return RunResult(False, "NoTests", "pytest collected no tests", seconds, rc)
        return RunResult(False, "PytestError", f"pytest exited with code {rc}", seconds, rc)

