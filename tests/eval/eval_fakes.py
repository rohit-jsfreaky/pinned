"""Shared fakes for the eval harness tests (fakes live only under tests/; unique module name, no package)."""

from __future__ import annotations

from pinned.eval.dataset import Problem


def make_problem(pid: str, library: str = "lib", version: str = "1.0", **kw) -> Problem:
    base = dict(id=pid, library=library, version=version, python_version="3.10", additional_dependencies="",
                problem=f"Task {pid}: implement f.", starting_code="def f():\n", type_of_change="name change",
                docs=(f"https://docs.example/{library}/{pid}.html",))
    base.update(kw)
    return Problem(**base)
