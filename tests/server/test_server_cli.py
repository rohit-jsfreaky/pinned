"""`pinned` CLI: every command, --json, the remote (--api) backend, exit codes, and helpful errors when HydraDB is down."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from server_fakes import free_port

from pinned import cli
from pinned.server.factory import load_factory
from pinned.server.errors import ServiceMisconfigured

HERE = Path(__file__).resolve().parent


@pytest.fixture()
def env(monkeypatch, graph, tmp_path):
    monkeypatch.setenv("HYDRA_URL", graph.url)
    monkeypatch.setenv("PINNED_FACTORY", "server_fakes:build_services")
    monkeypatch.setenv("PINNED_EVIDENCE_DIR", str(tmp_path / "evidence"))
    monkeypatch.delenv("PINNED_API", raising=False)


@pytest.fixture()
def code_file(tmp_path, lib):
    p = tmp_path / "job.py"
    p.write_text(f"import {lib}\nprint({lib}.old_sum([1]))\n")
    return p


def test_check_text_exit_1_on_problems(env, lib, code_file, capsys):
    assert cli.main(["check", str(code_file), "--lib", lib, "--version", "2.0.0"]) == cli.EXIT_PROBLEMS
    out = capsys.readouterr().out
    assert f"line 2: {lib}.old_sum: removed -> use {lib}.total" in out


def test_check_json_exit_0_when_valid(env, lib, code_file, capsys):
    assert cli.main(["check", str(code_file), "--lib", lib, "--version", "1.0.0", "--json"]) == cli.EXIT_OK
    d = json.loads(capsys.readouterr().out)
    assert d["ok"] is True and d["findings"][0]["status"] == "valid"


def test_json_flag_before_command(env, lib, capsys):
    assert cli.main(["--json", "libraries"]) == 0
    assert any(li["name"] == lib for li in json.loads(capsys.readouterr().out))


def test_how(env, lib, capsys):
    assert cli.main(["how", "sum the values", "--lib", lib, "--version", "2.0", "-k", "3"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"Facts for {lib}==2.0.0") and f"{lib}.total: valid" in out


def test_how_with_code_file_json(env, lib, code_file, capsys):
    assert cli.main(["how", "average", "--lib", lib, "--version", "2.0.0", "--code", str(code_file), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["items"][0]["path"] == f"{lib}.mean"


def test_explain(env, lib, capsys):
    assert cli.main(["explain", lib, f"{lib}.old_sum"]) == 0
    out = capsys.readouterr().out
    assert "- 1.0.0 .. 1.5.0: valid (x)" in out and "- 2.0.0: removed" in out and f"-> {lib}.total" in out


def test_stats(env, capsys):
    assert cli.main(["stats"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("graph: ") and "Api:" in out and "latency (search): no saved run yet" in out
    assert cli.main(["stats", "--json"]) == 0
    assert "graph" in json.loads(capsys.readouterr().out)


def test_input_errors_exit_2(env, lib, capsys, tmp_path):
    assert cli.main(["check", str(tmp_path / "nope.py"), "--lib", lib, "--version", "1.0"]) == cli.EXIT_INPUT
    assert "no such file" in capsys.readouterr().err
    assert cli.main(["explain", lib, f"{lib}.nothing"]) == cli.EXIT_INPUT
    assert "no facts" in capsys.readouterr().err
    assert cli.main(["how", "x", "--lib", lib, "--version", "7.0"]) == cli.EXIT_INPUT
    err = capsys.readouterr().err
    assert "not in the Pinned graph" in err and "known_versions" in err


def test_hydradb_down_is_helpful(monkeypatch, lib, code_file, capsys):
    monkeypatch.setenv("HYDRA_URL", f"http://127.0.0.1:{free_port()}")
    monkeypatch.setenv("PINNED_FACTORY", "server_fakes:build_services")
    assert cli.main(["check", str(code_file), "--lib", lib, "--version", "2.0"]) == cli.EXIT_UNAVAILABLE
    err = capsys.readouterr().err
    assert "HydraDB is not reachable" in err and "infra/hydradb-up.sh" in err and "--api" in err


def test_default_factory_without_parts_is_explained(monkeypatch, capsys):
    """Until parts B/C/D merge, the default factory must fail with a clear message, not a traceback."""
    monkeypatch.delenv("PINNED_FACTORY", raising=False)
    try:
        import pinned.search  # noqa: F401
        pytest.skip("part C is installed; the real factory is covered by integration")
    except ImportError:
        pass
    assert cli.main(["stats"]) == cli.EXIT_UNAVAILABLE
    assert "not installed" in capsys.readouterr().err


def test_load_factory_errors():
    for spec in ("nocolon", "no.such.module:x", "pinned.server.config:missing", "pinned.server.config:ROOT"):
        with pytest.raises(ServiceMisconfigured):
            load_factory(spec)


def test_remote_backend_against_running_server(graph, lib, code_file, capsys, tmp_path, monkeypatch):
    from server_fakes import GraphBackedSearch, RecordingLearner
    from test_server_mcp import _Server

    from pinned.server import Settings, create_app

    monkeypatch.delenv("HYDRA_URL", raising=False)
    s = Settings(evidence_dir=tmp_path, maintain_every=0)
    with _Server(create_app(GraphBackedSearch(graph), RecordingLearner(graph), graph, s)) as srv:
        api = f"http://127.0.0.1:{srv.port}"
        assert cli.main(["--api", api, "check", str(code_file), "--lib", lib, "--version", "2.0"]) == 1
        assert f"-> use {lib}.total" in capsys.readouterr().out
        assert cli.main(["how", "sum", "--lib", lib, "--version", "2.0", "--api", api]) == 0
        assert f"{lib}.total" in capsys.readouterr().out
        assert cli.main(["explain", lib, f"{lib}.total", "--api", api, "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["versions"][0]["status"] == "added_later"
        assert cli.main(["stats", "--api", api]) == 0 and "graph:" in capsys.readouterr().out
        assert cli.main(["libraries", "--api", api]) == 0 and lib in capsys.readouterr().out
        assert cli.main(["how", "x", "--lib", lib, "--version", "9.0", "--api", api]) == cli.EXIT_INPUT
        assert "not in the Pinned graph" in capsys.readouterr().err
    assert cli.main(["stats", "--api", f"http://127.0.0.1:{free_port()}"]) == cli.EXIT_UNAVAILABLE
    assert "cannot reach the Pinned server" in capsys.readouterr().err


def test_installed_entry_point_subprocess(graph, lib, code_file):
    """The real `pinned` console script, as a user runs it."""
    exe = Path(sys.executable).with_name("pinned")
    env = {**os.environ, "HYDRA_URL": graph.url, "PINNED_FACTORY": "server_fakes:build_services",
           "PYTHONPATH": str(HERE)}
    r = subprocess.run([str(exe), "check", str(code_file), "--lib", lib, "--version", "2.0.0", "--json"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 1, r.stderr
    assert json.loads(r.stdout)["findings"][0]["fix"] == f"{lib}.total"
    r = subprocess.run([str(exe), "--help"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and all(c in r.stdout for c in ("serve", "mcp", "check", "how", "explain", "stats"))


def test_serve_starts_and_answers(graph, lib, tmp_path):
    """`pinned serve` as a subprocess: /healthz, /v1/libraries and /mcp all on one port."""
    import time

    import httpx

    port = free_port()
    env = {**os.environ, "HYDRA_URL": graph.url, "PINNED_FACTORY": "server_fakes:build_services",
           "PYTHONPATH": str(HERE), "PINNED_EVIDENCE_DIR": str(tmp_path)}
    p = subprocess.Popen([sys.executable, "-m", "pinned.cli", "serve", "--port", str(port), "--log-level", "warning"],
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(100):
            try:
                if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise AssertionError(p.stderr.read() if p.poll() is not None else "server did not answer")
        libs = httpx.get(f"http://127.0.0.1:{port}/v1/libraries").json()["libraries"]
        assert any(li["name"] == lib for li in libs)
        r = httpx.post(f"http://127.0.0.1:{port}/mcp", json={"jsonrpc": "2.0", "id": 7, "method": "ping"},
                       headers={"Accept": "application/json, text/event-stream"})
        assert r.status_code == 200 and r.json()["id"] == 7
    finally:
        p.terminate()
        p.wait(10)
