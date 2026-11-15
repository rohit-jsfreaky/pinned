"""Solver: code extraction, cache keys, prompt contents, CLI output parsing, cached + rate-limited solving."""

import json

import pytest

from pinned.eval.solver import (
    TEMPLATE_HASH, Backoff, ClaudeCLISolver, CompletionCache, RateLimited, SolveRequest, SolverError,
    SolverOutput, cache_key, extract_code, parse_cli_output, prompt_hash, render_prompt, solve_many,
)

from eval_fakes import make_problem


@pytest.mark.parametrize("text,code", [
    ("```python\nx = 1\n```", "x = 1\n"),
    ("Here:\n```py\nx = 1\n```\nthanks", "x = 1\n"),
    ("```\nx = 1\n```", "x = 1\n"),
    ("```python\na=1\n```\nand\n```python\nimport os\ndef f():\n    return 2\n```", "import os\ndef f():\n    return 2\n"),
    ("```bash\npip install x\n```\n```python\ny = 2\n```", "y = 2\n"),
    ("x = 1\n", "x = 1\n"),
    ("```python\nx = 1\n", "x = 1\n"),     # unterminated fence
    ("", ""),
])
def test_extract_code(text, code):
    assert extract_code(text) == code


def test_cache_key_depends_on_every_part():
    base = cache_key("none", "1", 0, "h")
    assert base == cache_key("none", "1", 0, "h")
    assert len({base, cache_key("docs", "1", 0, "h"), cache_key("none", "2", 0, "h"),
                cache_key("none", "1", 1, "h"), cache_key("none", "1", 0, "h2")}) == 5
    assert prompt_hash("a") != prompt_hash("b")


def test_prompt_has_only_solver_fields_and_one_template():
    p = make_problem("3", "scipy", "1.7.3", additional_dependencies="numpy==1.21.6", type_of_change="SECRET_TOC",
                     docs=("https://SECRET.docs/x",))
    plain = render_prompt(p)
    with_ctx = render_prompt(p, "CONTEXT BLOCK")
    for s in ("scipy==1.7.3", "numpy==1.21.6", "Python 3.10", p.problem, "def f():"):
        assert s in plain
    assert "SECRET_TOC" not in plain and "SECRET.docs" not in plain
    assert "CONTEXT BLOCK" in with_ctx and "CONTEXT BLOCK" not in plain
    # same template: removing the context block gives back the plain prompt
    assert with_ctx.replace("\nCONTEXT BLOCK\n", "") == plain
    assert len(TEMPLATE_HASH) == 12


def _cli(**kw) -> str:
    d = {"type": "result", "subtype": "success", "is_error": False, "result": "```python\nx=1\n```",
         "total_cost_usd": 0.001, "usage": {"input_tokens": 5, "cache_read_input_tokens": 10, "output_tokens": 7},
         "modelUsage": {"claude-haiku-5-5": {}}}
    d.update(kw)
    return json.dumps(d)


def test_parse_cli_output():
    out = parse_cli_output(_cli(), "", 0, 1.234)
    assert out.text.startswith("```python") and out.model == "claude-haiku-5-5"
    assert out.input_tokens == 15 and out.output_tokens == 7 and out.seconds == 1.23
    with pytest.raises(RateLimited):
        parse_cli_output(_cli(is_error=True, api_error_status=429, result="API Error"), "", 1, 1)
    with pytest.raises(RateLimited):
        parse_cli_output(_cli(is_error=True, result="Claude AI usage limit reached|1700000000"), "", 1, 1)
    with pytest.raises(SolverError):
        parse_cli_output(_cli(is_error=True, result="something else", api_error_status=500), "", 1, 1)
    with pytest.raises(SolverError):
        parse_cli_output("not json", "boom", 1, 1)


def test_cli_command_has_no_tools_and_no_project_settings():
    cmd = ClaudeCLISolver(binary="claude").command()
    assert cmd[1:5] == ["-p", "--model", "haiku", "--output-format"]
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert "--no-session-persistence" in cmd and "--strict-mcp-config" in cmd


class FakeSolver:
    name = "fake"

    def __init__(self, fail_first: int = 0, rate_limits: int = 0):
        self.calls = 0
        self.fail_first = fail_first
        self.rate_limits = rate_limits

    def describe(self):
        return {"backend": "fake"}

    def complete(self, prompt: str) -> SolverOutput:
        self.calls += 1
        if self.rate_limits:
            self.rate_limits -= 1
            raise RateLimited("429")
        if self.fail_first:
            self.fail_first -= 1
            raise SolverError("flaky")
        return SolverOutput(f"```python\n# {len(prompt)}\n```", model="fake-model")


def test_solve_many_caches_and_backs_off(tmp_path):
    cache = CompletionCache(tmp_path)
    reqs = [SolveRequest("none", str(i), 0, f"prompt {i}") for i in range(4)]
    got = {}
    sleeps = []
    s = FakeSolver(rate_limits=2)
    solve_many(reqs, s, cache, lambda r, c, e: got.__setitem__(r.problem_id, (c, e)), concurrency=1,
               backoff=Backoff(base_s=0.01, max_s=0.02), sleep=sleeps.append, log=lambda m: None)
    assert all(c is not None and not e for c, e in got.values())
    assert s.calls == 6   # 2 rate limits + 4 successes
    s2 = FakeSolver()
    solve_many(reqs, s2, cache, lambda r, c, e: None, concurrency=2, log=lambda m: None)
    assert s2.calls == 0  # reruns are free
    assert cache.get(reqs[0].key).code.startswith("# ")


def test_solve_many_gives_up_and_reports(tmp_path):
    got = {}
    s = FakeSolver(fail_first=10)
    solve_many([SolveRequest("none", "1", 0, "p")], s, CompletionCache(tmp_path),
               lambda r, c, e: got.__setitem__(r.problem_id, (c, e)), backoff=Backoff(error_attempts=2),
               sleep=lambda x: None, log=lambda m: None)
    comp, err = got["1"]
    assert comp is None and err.startswith("SolverError")
    assert CompletionCache(tmp_path).get(SolveRequest("none", "1", 0, "p").key) is None   # failures not cached
