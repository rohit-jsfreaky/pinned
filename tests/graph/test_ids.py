import pytest

from pinned.graph import node_id, version_ord


def test_node_id_stable_and_63_bit():
    assert node_id("Api", "pandas|pandas.concat") == node_id("Api", "pandas|pandas.concat")
    assert node_id("Api", "x") != node_id("Term", "x")
    assert 0 <= node_id("Api", "x") < 2**63


def test_version_ord_orders_like_pep440():
    vs = ["0.9", "1.0.dev1", "1.0a1", "1.0b2", "1.0rc1", "1.0", "1.0.1", "1.2", "1.10", "2.0.0", "10.0"]
    assert sorted(vs, key=version_ord) == vs
    assert version_ord("2.0.0") == 2_000_000_999
    assert version_ord("1.21.6") == 1_021_006_999


def test_version_ord_rejects_unfittable():
    with pytest.raises(ValueError):
        version_ord("1.1000")
