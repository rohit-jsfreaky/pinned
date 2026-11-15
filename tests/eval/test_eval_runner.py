"""Runner: pass / fail / timeout / error trimming, and the hidden test source never leaks into error_text."""

import sys
from pathlib import Path

import pytest

from pinned.eval.runner import WITHHELD, run_candidate, scrub

PY = Path(sys.executable)   # the project venv has pytest; the runner only needs an interpreter with pytest

SECRET = "EXPECTED_SECRET_VALUE_42"
TEST = f'''
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from sample_7 import f

def helper_in_test(x):
    return f(x)

def test_value():
    assert helper_in_test(2) == "{SECRET}", "expected {SECRET}"
'''


def _test_file(tmp_path: Path, src: str = TEST) -> Path:
    p = tmp_path / "test_sample_7.py"
    p.write_text(src)
    return p


def test_pass(tmp_path):
    r = run_candidate("7", f'def f(x):\n    return "{SECRET}"\n', PY, _test_file(tmp_path), timeout=60)
    assert r.passed and r.error_type == "" and r.error_text == "" and r.exit_code == 0


def test_assertion_in_hidden_test_is_withheld(tmp_path):
    r = run_candidate("7", "def f(x):\n    return 'nope'\n", PY, _test_file(tmp_path), timeout=60)
    assert not r.passed and r.error_type == "AssertionError"
    assert SECRET not in r.error_text and "helper_in_test" not in r.error_text
    assert "withheld" in r.error_text


def test_exception_keeps_only_solution_frames(tmp_path):
    code = "import os\n\ndef inner(x):\n    return os.path.nonexistent_api(x)\n\ndef f(x):\n    return inner(x)\n"
    r = run_candidate("7", code, PY, _test_file(tmp_path), timeout=60)
    assert not r.passed and r.error_type == "AttributeError"
    assert 'File "solution.py", line 4, in inner' in r.error_text
    assert 'File "solution.py", line 7, in f' in r.error_text
    assert "nonexistent_api" in r.error_text
    assert "test_sample" not in r.error_text and "helper_in_test" not in r.error_text
    assert "sample_7" not in r.error_text and str(tmp_path) not in r.error_text


def test_syntax_and_import_errors(tmp_path):
    r = run_candidate("7", "def f(x:\n", PY, _test_file(tmp_path), timeout=60)
    assert r.error_type == "SyntaxError" and 'File "solution.py", line 1' in r.error_text
    assert "from sample_7 import" not in r.error_text
    r = run_candidate("7", "def g():\n    pass\n", PY, _test_file(tmp_path), timeout=60)
    assert r.error_type == "ImportError" and "cannot import name 'f'" in r.error_text
    assert "pinned-run-" not in r.error_text
    r = run_candidate("7", "import not_a_module_xyz\n", PY, _test_file(tmp_path), timeout=60)
    assert r.error_type == "ModuleNotFoundError" and "import not_a_module_xyz" in r.error_text


def test_timeout(tmp_path):
    r = run_candidate("7", "import time\ndef f(x):\n    time.sleep(60)\n", PY, _test_file(tmp_path), timeout=3)
    assert not r.passed and r.error_type == "Timeout" and r.seconds < 30


def test_message_echoing_test_source_is_scrubbed(tmp_path):
    # the test passes a literal that is a whole line of its own source; the solution echoes it in an exception
    test = _test_file(tmp_path, TEST.replace("helper_in_test(2)", f'helper_in_test("{SECRET} long literal")'))
    r = run_candidate("7", "def f(x):\n    raise ValueError('bad: ' + x)\n", PY, test, timeout=60)
    assert r.error_type == "ValueError"
    for line in test.read_text().splitlines():
        if len(line.strip()) >= 8:
            assert line.strip() not in r.error_text


def test_scrub_removes_every_test_line():
    src = "def test_x():\n    assert compute(3) == 99, 'nope'\n"
    text = "boom\n    assert compute(3) == 99, 'nope'\nmore"
    assert scrub(text, src) == f"boom\n{WITHHELD}\nmore"


def test_missing_interpreter_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        run_candidate("7", "", tmp_path / "nope" / "python", _test_file(tmp_path))
