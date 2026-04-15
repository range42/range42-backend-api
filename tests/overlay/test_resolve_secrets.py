import pytest

from app.overlay import resolve_secrets
from app.overlay.resolve_secrets import SecretLookup
from app.core.redaction import VAULT_MARKER


class DictLookup(SecretLookup):
    def __init__(self, d): self._d = d
    def get(self, name): return self._d.get(name)


def test_trivial_empty_vault():
    """Edge vector: env declared but no *_from refs -> identity."""
    doc = {
        "schema_version": "1.0", "kind": "lab", "name": "x",
        "env": [{"name": "admin_password", "scope": "per_team",
                 "secret": True, "required": True}],
    }
    assert resolve_secrets(doc, DictLookup({})) == doc


def test_trivial_no_env():
    """Edge vector: no env, no refs -> identity."""
    doc = {"schema_version": "1.0", "kind": "lab", "name": "x"}
    assert resolve_secrets(doc, DictLookup({"admin_password": "hunter2"})) == doc


def test_edge_env_and_vault_passes_without_refs():
    """Edge vector (previously Plan A not-implemented): with env and vault
    populated but no *_from references, resolve_secrets is now an identity
    transform. First-class pass vector."""
    doc = {
        "schema_version": "1.0", "kind": "lab", "name": "x",
        "env": [{"name": "admin_password", "secret": True, "required": True}],
    }
    assert resolve_secrets(doc, DictLookup({"admin_password": "hunter2"})) == doc


def test_resolves_env_secret_refs():
    doc = {
        "env": [{"name": "db_pass", "secret": True, "scope": "per_team"}],
        "nodes": [{"id": "n1", "kind": "vm", "attachments": [
            {"source": {"kind": "catalog_role", "ref": "x"}, "stage": "install",
             "vars": {"admin_password_from": "env.secret.db_pass"}}
        ]}]
    }
    out = resolve_secrets(doc, DictLookup({"db_pass": "s3cret"}))
    admin = out["nodes"][0]["attachments"][0]["vars"]["admin_password"]
    assert admin == {VAULT_MARKER: True, "value": "s3cret"}
    assert "admin_password_from" not in out["nodes"][0]["attachments"][0]["vars"]


def test_missing_secret_raises():
    doc = {"env": [{"name": "db_pass", "secret": True}],
           "nodes": [{"id": "n", "kind": "vm", "attachments": [
               {"source": {"kind": "catalog_role", "ref": "x"},
                "stage": "install",
                "vars": {"admin_password_from": "env.secret.db_pass"}}
           ]}]}
    with pytest.raises(KeyError):
        resolve_secrets(doc, DictLookup({}))
