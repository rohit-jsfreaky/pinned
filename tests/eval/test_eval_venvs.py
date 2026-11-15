import hashlib

from pinned.eval.venvs import ERA_CONSTRAINTS, VenvSpec, specs_for, venv_key

from eval_fakes import make_problem


def test_key_matches_upstream_formula():
    # upstream gitchameleon/utils.py generate_venv_cache_key
    expected = hashlib.sha256("-".join(["3.7", "torch", "1.9.0", "scipy==1.7.3 numpy==1.21.6"]).encode()).hexdigest()
    assert venv_key("3.7", "torch", "1.9.0", "scipy==1.7.3 numpy==1.21.6") == expected[:12]
    assert VenvSpec("3.7", "torch", "1.9.0", "scipy==1.7.3 numpy==1.21.6").dirname == f"gcham_venv_{expected[:12]}"


def test_dedupe_by_python_library_version_and_deps():
    ps = [
        make_problem("1", "scipy", "1.7.3"),
        make_problem("2", "scipy", "1.7.3"),                                   # same venv
        make_problem("3", "scipy", "1.7.3", additional_dependencies="numpy==1.21.6"),
        make_problem("4", "scipy", "1.7.3", python_version="3.9"),
        make_problem("5", "scipy", "1.8.0"),
    ]
    specs = specs_for(ps)
    assert len(specs) == 4
    assert VenvSpec.of(ps[0]).key == VenvSpec.of(ps[1]).key


def test_install_steps_follow_upstream_order_and_substring_rule():
    s = VenvSpec("3.10", "flask", "2.0.0", "numpy==1.21.6 werkzeug==2.0.0")
    assert s.install_steps() == [
        ["flask==2.0.0", "numpy==1.21.6", "werkzeug==2.0.0"],
        ["pytest==7.2.0"], ["pytest-cov==4.1.0"], ["scipy==1.10.1"],   # numpy named in deps -> no numpy pin
    ]
    np_lib = VenvSpec("3.7", "numpy", "1.21.0", "")
    assert ["numpy==1.21.6"] not in np_lib.install_steps()
    assert ["scipy==1.7.1"] in np_lib.install_steps()
    assert VenvSpec("3.10", "pytest", "7.0.0", "").install_steps()[1:] == [["numpy==1.23"], ["scipy==1.10.1"]]
    assert VenvSpec("3.7", "x", "1", "pip=20.0 - foo==1").deps() == ["foo==1"]


def test_torch_cpu_index_and_era_constraints():
    assert VenvSpec("3.7", "kymatio", "0.3.0", "torch==1.4.0").needs_torch_index()
    assert VenvSpec("3.10", "torch", "2", "").needs_torch_index()
    assert not VenvSpec("3.10", "scipy", "1.8.0", "").needs_torch_index()
    assert VenvSpec("3.10", "flask", "3.0.0", "").constraints() == ERA_CONSTRAINTS[("flask", "3.0.0")]
    assert VenvSpec("3.10", "flask", "2.0.0", "").constraints() == ()
