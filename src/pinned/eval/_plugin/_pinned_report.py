"""pytest plugin loaded INSIDE a problem venv (`-p _pinned_report`). Must run on Python 3.7+ and pytest 6.2+ with only
the standard library. It writes every exception pytest saw (collection or test phase) to the JSON file named by
$PINNED_REPORT: type, message, and the traceback frames (file, line, function, source line). The parent process
(pinned.eval.runner) filters frames to the solution file and scrubs anything taken from the hidden test."""

import json
import os
import traceback

_EVENTS = []


def _frames(tb):
    out = []
    for fs in traceback.extract_tb(tb):
        out.append({"file": fs.filename, "line": fs.lineno, "name": fs.name, "code": fs.line or ""})
    return out


def pytest_exception_interact(node, call, report):
    excinfo = getattr(call, "excinfo", None)
    if excinfo is None:
        return
    value = excinfo.value
    tb = excinfo.tb
    # pytest wraps import/syntax errors of the test module in CollectError (whose text quotes the test source);
    # report the original exception instead.
    while type(value).__name__ == "CollectError" and getattr(value, "__cause__", None) is not None:
        value = value.__cause__
        tb = value.__traceback__
    ev = {
        "when": getattr(call, "when", "collect"),
        "nodeid": getattr(node, "nodeid", ""),
        "type": type(value).__name__,
        "module": type(value).__module__,
        "message": str(value),
        "frames": _frames(tb),
    }
    if isinstance(value, SyntaxError):
        ev["syntax"] = {"file": value.filename or "", "line": value.lineno or 0, "text": (value.text or "").rstrip()}
    _EVENTS.append(ev)


def pytest_runtest_logreport(report):
    if report.when == "call" and report.passed:
        _EVENTS.append({"when": "call", "nodeid": report.nodeid, "passed": True})


def pytest_sessionfinish(session, exitstatus):
    path = os.environ.get("PINNED_REPORT")
    if not path:
        return
    with open(path, "w") as f:
        json.dump({"exitstatus": int(exitstatus), "events": _EVENTS}, f)
