"""The solver: one prompt template for every arm, `claude -p` as the model, an on-disk completion cache.

Every arm gets the SAME template; only the context block differs. The solver sees only the fields in
`Problem.solver_view()` plus the arm's context text (never the answer key).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from .dataset import Problem

SYSTEM_PROMPT = (
    "You are an expert Python programmer. You write code that runs correctly with the exact library versions you are "
    "given. Reply with code only."
)

TEMPLATE = """Write Python code for the task below.

Environment (exact versions; use only APIs that exist in them):
- Python {python_version}
- {library}=={version}
- other packages: {additional_dependencies}
{context_block}
Task:
{problem}

Complete this starting code. Keep its imports and the given function or class names and signatures:
```python
{starting_code}
```

Reply with the complete Python solution in a single ```python code block and nothing else."""

TEMPLATE_HASH = hashlib.sha256((SYSTEM_PROMPT + "\x00" + TEMPLATE).encode()).hexdigest()[:12]
DEFAULT_CACHE_DIR = Path("data/eval_cache/completions")


def render_prompt(problem: Problem, context_text: str = "") -> str:
    view = problem.solver_view()
    block = f"\n{context_text.strip()}\n" if context_text.strip() else ""
    return TEMPLATE.format(
        python_version=view["python_version"],
        library=view["library"],
        version=view["version"],
        additional_dependencies=view["additional_dependencies"] or "none pinned",
        context_block=block,
        problem=view["problem"].strip(),
        starting_code=view["starting_code"].rstrip("\n"),
    )


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256((TEMPLATE_HASH + "\x00" + prompt).encode()).hexdigest()[:16]


def cache_key(arm: str, problem_id: str, iteration: int, p_hash: str) -> str:
    return hashlib.sha256(f"{arm}\x00{problem_id}\x00{iteration}\x00{p_hash}".encode()).hexdigest()[:24]


_FENCE = re.compile(r"```[ \t]*([A-Za-z0-9_+-]*)[^\n]*\n(.*?)```", re.DOTALL)


def extract_code(text: str) -> str:
    """The code the model wrote. Fenced blocks tagged python/py (or untagged) win; among them the longest. No fence ->
    the whole reply (an unterminated opening fence is dropped)."""
    blocks = [(lang.lower(), body) for lang, body in _FENCE.findall(text)]
    py = [b for lang, b in blocks if lang in ("python", "py", "python3", "")]
    if py:
        return max(py, key=len).strip("\n") + "\n"
    if blocks:
        return max((b for _, b in blocks), key=len).strip("\n") + "\n"
    stripped = re.sub(r"^```[A-Za-z0-9_+-]*\s*\n", "", text.strip())
    return stripped.strip("\n") + "\n" if stripped.strip() else ""


# ---------------------------------------------------------------- model backends
@dataclass
class SolverOutput:
    text: str
    model: str = ""
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0


class RateLimited(RuntimeError):
    pass


class SolverError(RuntimeError):
    pass


class Solver(Protocol):
    name: str

    def describe(self) -> dict: ...
    def complete(self, prompt: str) -> SolverOutput: ...


_RATE_WORDS = ("rate limit", "rate_limit", "usage limit", "overloaded", "too many requests", "429", "529")


class ClaudeCLISolver:
    """`claude -p --model <alias> --output-format json`, prompt on stdin, one fresh session per call, no tools, no
    settings / CLAUDE.md / MCP from any project (runs in an empty temp dir with --setting-sources "")."""

    name = "claude-cli"

    def __init__(self, model: str = "haiku", binary: str = "claude", timeout: float = 300.0):
        self.model = model
        self.binary = shutil.which(binary) or binary
        self.timeout = timeout
        self._version: str | None = None

    def command(self) -> list[str]:
        return [self.binary, "-p", "--model", self.model, "--output-format", "json", "--tools", "",
                "--system-prompt", SYSTEM_PROMPT, "--no-session-persistence", "--setting-sources", "",
                "--strict-mcp-config", "--disable-slash-commands"]

    def version(self) -> str:
        if self._version is None:
            res = subprocess.run([self.binary, "--version"], capture_output=True, text=True, timeout=60)
            if res.returncode != 0:
                raise SolverError(f"`claude --version` failed: {res.stderr.strip()[:300]}")
            self._version = res.stdout.strip()
        return self._version

    def describe(self) -> dict:
        cmd = self.command()
        flags = [c if c != SYSTEM_PROMPT else "<SYSTEM_PROMPT>" for c in cmd[1:]]
        return {"backend": self.name, "cli_version": self.version(), "model_alias": self.model, "flags": flags,
                "temperature": "CLI default (claude -p has no temperature flag)"}

    def complete(self, prompt: str) -> SolverOutput:
        t0 = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="pinned-solve-") as cwd:
            try:
                res = subprocess.run(self.command(), input=prompt, capture_output=True, text=True, cwd=cwd,
                                     timeout=self.timeout)
            except subprocess.TimeoutExpired as e:
                raise SolverError(f"claude -p timed out after {self.timeout:g}s") from e
        return parse_cli_output(res.stdout, res.stderr, res.returncode, time.monotonic() - t0)


def parse_cli_output(stdout: str, stderr: str, returncode: int, seconds: float) -> SolverOutput:
    try:
        data = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        blob = (stdout + " " + stderr).lower()
        if any(w in blob for w in _RATE_WORDS):
            raise RateLimited((stderr or stdout).strip()[:300])
        raise SolverError(f"claude -p exit {returncode}, no JSON: {(stderr or stdout).strip()[:300]}")
    text = str(data.get("result") or "")
    if data.get("is_error") or data.get("subtype") not in (None, "success"):
        status = data.get("api_error_status")
        blob = f"{text} {status}".lower()
        if status in (429, 529) or any(w in blob for w in _RATE_WORDS):
            raise RateLimited(text[:300] or str(status))
        raise SolverError(f"claude -p error ({data.get('subtype')}, status {status}): {text[:300]}")
    models = list((data.get("modelUsage") or {}).keys())
    usage = data.get("usage") or {}
    return SolverOutput(
        text=text,
        model=",".join(models),
        cost_usd=float(data.get("total_cost_usd") or 0.0),
        input_tokens=int(usage.get("input_tokens") or 0) + int(usage.get("cache_read_input_tokens") or 0)
        + int(usage.get("cache_creation_input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        seconds=round(seconds, 2),
    )


# ---------------------------------------------------------------- cache
@dataclass
class Completion:
    key: str                 # cache key: (arm, problem, iteration, prompt hash)
    arm: str
    problem_id: str
    iteration: int
    prompt_hash: str
    template_hash: str
    text: str                # raw model reply
    code: str                # extracted code
    model: str = ""
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    extra: dict = field(default_factory=dict)


class CompletionCache:
    """One JSON file per completion under `data/eval_cache/completions/<k[:2]>/<k>.json`. Reruns are free."""

    def __init__(self, root: Path = DEFAULT_CACHE_DIR):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> Completion | None:
        p = self._path(key)
        if not p.exists():
            return None
        try:
            return Completion(**json.loads(p.read_text()))
        except (json.JSONDecodeError, TypeError):
            return None

    def put(self, c: Completion) -> None:
        p = self._path(c.key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(c)))
        tmp.replace(p)


# ---------------------------------------------------------------- many requests, small concurrency, backoff
@dataclass(frozen=True)
class SolveRequest:
    arm: str
    problem_id: str
    iteration: int
    prompt: str

    @property
    def prompt_hash(self) -> str:
        return prompt_hash(self.prompt)

    @property
    def key(self) -> str:
        return cache_key(self.arm, self.problem_id, self.iteration, self.prompt_hash)


@dataclass
class Backoff:
    base_s: float = 30.0
    max_s: float = 900.0
    max_attempts: int = 10          # rate-limit retries per request before giving up (the run is resumable)
    error_attempts: int = 3         # other solver errors


def solve_many(requests: list[SolveRequest], solver: Solver, cache: CompletionCache,
               on_done: Callable[[SolveRequest, Completion | None, str], None], concurrency: int = 3,
               backoff: Backoff | None = None, sleep: Callable[[float], None] = time.sleep, log=print) -> None:
    """Calls `on_done(req, completion, error)` for every request (completion None + error text if it failed).
    Cached requests return immediately. On a rate limit every worker pauses (shared gate) with exponential backoff."""
    backoff = backoff or Backoff()
    gate_lock = threading.Lock()
    gate = {"until": 0.0, "streak": 0}
    out_lock = threading.Lock()

    def wait_gate() -> None:
        while True:
            with gate_lock:
                delay = gate["until"] - time.monotonic()
            if delay <= 0:
                return
            sleep(min(delay, 30.0))

    def one(req: SolveRequest) -> tuple[Completion | None, str]:
        hit = cache.get(req.key)
        if hit is not None:
            return hit, ""
        rl, errs = 0, 0
        while True:
            wait_gate()
            try:
                out = solver.complete(req.prompt)
            except RateLimited as e:
                rl += 1
                with gate_lock:
                    gate["streak"] += 1
                    pause = min(backoff.max_s, backoff.base_s * 2 ** min(gate["streak"] - 1, 8))
                    gate["until"] = max(gate["until"], time.monotonic() + pause)
                log(f"  rate limited ({e}); pausing {pause:.0f}s [attempt {rl}/{backoff.max_attempts}]")
                if rl >= backoff.max_attempts:
                    return None, f"RateLimited: {e}"
                continue
            except SolverError as e:
                errs += 1
                if errs >= backoff.error_attempts:
                    return None, f"SolverError: {e}"
                sleep(min(backoff.base_s, 5.0 * errs))
                continue
            with gate_lock:
                gate["streak"] = 0
            c = Completion(key=req.key, arm=req.arm, problem_id=req.problem_id, iteration=req.iteration,
                           prompt_hash=req.prompt_hash, template_hash=TEMPLATE_HASH, text=out.text,
                           code=extract_code(out.text), model=out.model, cost_usd=out.cost_usd,
                           input_tokens=out.input_tokens, output_tokens=out.output_tokens, seconds=out.seconds)
            cache.put(c)
            return c, ""

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futs = {pool.submit(one, r): r for r in requests}
        for fut in as_completed(futs):
            comp, err = fut.result()
            with out_lock:
                on_done(futs[fut], comp, err)
