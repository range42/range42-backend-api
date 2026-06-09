"""Tests for VaultTaggedLayer (spec §13 hard invariant)."""
from app.core.redaction import VAULT_MARKER, VaultTaggedLayer


def test_redacts_marked_values():
    layer = VaultTaggedLayer()
    event = {
        "payload": {
            "vars": {
                "db_password": {VAULT_MARKER: True, "value": "s3cret"},
                "ok": "plain",
            }
        }
    }
    out, fired = layer.redact(event)
    assert out["payload"]["vars"]["db_password"] == "[REDACTED:vault_tagged]"
    assert out["payload"]["vars"]["ok"] == "plain"
    assert fired == [
        {"rule_id": "vault:tagged-value", "field_path": "payload.vars.db_password"}
    ]


def test_redacts_yaml_vault_string_prefix():
    layer = VaultTaggedLayer()
    vaulted = "!vault |\n  $ANSIBLE_VAULT;1.1;AES256\n  66663333..."
    event = {"payload": {"x": vaulted}}
    out, fired = layer.redact(event)
    assert out["payload"]["x"] == "[REDACTED:vault_tagged]"
    assert fired[0]["rule_id"] == "vault:ansible-vault-inline"
