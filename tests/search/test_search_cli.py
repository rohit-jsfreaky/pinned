"""The search CLI end to end (plain and --json) on a fixture graph."""

import json

from pinned.search.cli import main


def _demo(lib):
    b = lib(("0.25.0", "1.3.5", "2.0.0"))
    m = b.imp
    b.api("DataFrame", kind="class", added=[("0.25.0", "(data=None)")])
    b.api("DataFrame.append", kind="method", added=[("0.25.0", "(other, ignore_index=False)")], removed=["2.0.0"])
    b.api("concat", added=[("0.25.0", "(objs, axis=0, ignore_index=False)")])
    b.replace("DataFrame.append", "concat", evidence="use concat instead")
    b.cue("append", "DataFrame.append").cue("rows", "concat", w=0.5).commit()
    return b, m


def test_cli_how_check_explain(lib, graph, tmp_path, capsys):
    b, m = _demo(lib)
    url = ["--url", graph.url]

    assert main(url + ["how", "append rows to a frame", "--lib", b.name, "--version", "2.0.0"]) == 0
    out = capsys.readouterr().out
    assert f"{m}.DataFrame.append  [removed]" in out and f"-REPLACED_BY-> {m}.concat" in out

    src = tmp_path / "x.py"
    src.write_text(f"import {m} as pd\ndf = pd.DataFrame()\ndf = df.append(df, ignore_index=True)\n")
    assert main(url + ["check", str(src), "--lib", b.name, "--version", "2.0.0"]) == 0
    out = capsys.readouterr().out
    assert f"line 3: {m}.DataFrame.append  [removed]  fix: {m}.concat" in out

    assert main(url + ["--json", "check", str(src), "--lib", b.name, "--version", "1.3.5"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {d["call"]: d["status"] for d in data} == {f"{m}.DataFrame": "valid", f"{m}.DataFrame.append": "valid"}

    assert main(url + ["explain", b.name, f"{m}.DataFrame.append", "--json"]) == 0
    t = json.loads(capsys.readouterr().out)
    assert [v["status"] for v in t["versions"]] == ["valid", "valid", "removed"]
    assert main(url + ["explain", b.name, f"{m}.DataFrame.append"]) == 0
    assert "removed" in capsys.readouterr().out


def test_cli_errors(lib, graph, tmp_path, capsys):
    assert main(["--url", graph.url, "check", str(tmp_path / "nope.py"), "--lib", "x", "--version", "1"]) == 2
    assert main(["--url", "http://127.0.0.1:1", "explain", "x", "x.y"]) == 3
    assert "cannot reach the graph" in capsys.readouterr().err
