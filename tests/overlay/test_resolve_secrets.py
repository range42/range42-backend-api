import pytest

from app.overlay import NotImplementedOperator, resolve_secrets


def test_trivial_empty_vault():
    doc = {
        "schema_version": "1.0", "kind": "lab", "name": "x",
        "env": [{"name": "admin_password", "scope": "per_team",
                 "secret": True, "required": True}],
    }
    assert resolve_secrets(doc, {}) == doc


def test_trivial_no_env():
    doc = {"schema_version": "1.0", "kind": "lab", "name": "x"}
    assert resolve_secrets(doc, {"admin_password": "hunter2"}) == doc


def test_edge_env_and_vault_raises():
    doc = {
        "schema_version": "1.0", "kind": "lab", "name": "x",
        "env": [{"name": "admin_password", "secret": True, "required": True}],
    }
    with pytest.raises(NotImplementedOperator):
        resolve_secrets(doc, {"admin_password": "hunter2"})
