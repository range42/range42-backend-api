"""JS-semantics parity cases that the shared vectors do not cover.

These pin coercion rules where Python's natural spelling diverges from the
TS mirror — the kind of thing that only shows up on loosely-typed YAML.
"""
import pytest

from app.overlay.expand_replication import expand_replication


def _node(**kw):
    return {"nodes": [{"id": "a", **kw}]}


@pytest.mark.parametrize("scope", [False, 0, ""])
def test_falsy_but_non_null_scope_still_expands(scope):
    """TS uses `??`, which is nullish — only null/undefined mean shared.

    `or` would swallow false/0/"" into "shared" and silently drop every
    per-team copy.
    """
    got = expand_replication(_node(replication={"scope": scope}), 2)
    ids = [n["id"] for n in got["document"]["nodes"]]
    assert ids == ["a__team_1", "a__team_2"]


@pytest.mark.parametrize("scope", [None, "shared"])
def test_null_or_missing_scope_is_shared(scope):
    rep = {} if scope is None else {"scope": scope}
    got = expand_replication(_node(replication=rep), 2)
    assert [n["id"] for n in got["document"]["nodes"]] == ["a"]


def test_explicit_null_scope_is_shared():
    got = expand_replication(_node(replication={"scope": None}), 2)
    assert [n["id"] for n in got["document"]["nodes"]] == ["a"]


def test_fractional_vmid_offset_is_not_truncated():
    """int() on the offset gave every team the same vm_id — duplicate VMIDs."""
    got = expand_replication(
        _node(replication={"scope": "per_team", "id_offset": {"vmid": 0.5}},
              config={"vm_id": 100}),
        2,
    )
    assert [n["config"]["vm_id"] for n in got["document"]["nodes"]] == [100.5, 101]


def test_integral_vmid_offset_stays_int():
    got = expand_replication(
        _node(replication={"scope": "per_team", "id_offset": {"vmid": 4}},
              config={"vm_id": 100}),
        2,
    )
    assert [n["config"]["vm_id"] for n in got["document"]["nodes"]] == [104, 108]
