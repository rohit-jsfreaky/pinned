import json
from collections import Counter
from pathlib import Path

import pytest

from pinned.eval.dataset import DEFAULT_DATA_DIR, Problem, dataset_sha, load_problems
from pinned.eval.split import load_split, make_split, write_split_once

from eval_fakes import make_problem


def _problems() -> list[Problem]:
    out = []
    n = 0
    for lib, count in (("a", 10), ("b", 7), ("c", 1), ("d", 4)):
        for _ in range(count):
            out.append(make_problem(str(n), lib))
            n += 1
    return out


def test_split_is_deterministic_and_order_free():
    ps = _problems()
    s1 = make_split(ps, "sha", 13)
    s2 = make_split(list(reversed(ps)), "sha", 13)
    assert s1 == s2
    assert make_split(ps, "sha", 14) != s1   # the seed matters


def test_split_is_stratified_50_50_by_library_and_disjoint():
    ps = _problems()
    s = make_split(ps, "sha", 13)
    lib = {p.id: p.library for p in ps}
    tr, te = Counter(lib[i] for i in s.train), Counter(lib[i] for i in s.test)
    for name, n in Counter(p.library for p in ps).items():
        assert tr[name] == n // 2 and te[name] == n - n // 2
    assert not set(s.train) & set(s.test)
    assert set(s.train) | set(s.test) == {p.id for p in ps}
    assert s.of(s.train[0]) == "train" and s.of(s.test[0]) == "test"


def test_split_written_once(tmp_path: Path):
    ps = _problems()
    path = tmp_path / "split.json"
    s = make_split(ps, "sha", 13)
    write_split_once(s, path)
    write_split_once(s, path)                     # identical content: fine
    with pytest.raises(FileExistsError):
        write_split_once(make_split(ps, "sha", 99), path)
    assert load_split(path) == s
    assert json.loads(path.read_text())["seed"] == 13


@pytest.mark.skipif(not (DEFAULT_DATA_DIR / "dataset.jsonl").exists(), reason="dataset not downloaded")
def test_committed_split_matches_the_dataset():
    s = load_split(Path("evidence/split.json"))
    regenerated = make_split(load_problems(), dataset_sha(), 13)
    assert s == regenerated
    assert len(s.train) + len(s.test) == 328


def test_data_guard_keeps_pytest_out_of_data(tmp_path: Path):
    from pinned.eval.dataset import ensure_data_guard

    g = ensure_data_guard(tmp_path / "data")
    assert 'collect_ignore_glob = ["*"]' in g.read_text()
    assert ensure_data_guard(tmp_path / "data") == g
