"""Tests for ConfigDenylistLayer (spec §13 hard invariant)."""
from app.core.redaction import ConfigDenylistLayer


def test_denylist_redacts_suffix_match():
    layer = ConfigDenylistLayer(patterns=("*_password", "*_token", "*_key"))
    event = {
        "event_seq": 7,
        "payload": {
            "vars": {"admin_password": "s3cret", "api_token": "abc", "ok": "visible"},
            "nested": [{"ssh_key": "xxx"}, {"msg": "plain"}],
        },
    }
    redacted, fired = layer.redact(event)
    assert redacted["payload"]["vars"]["admin_password"] == "[REDACTED:config_denylist]"
    assert redacted["payload"]["vars"]["api_token"] == "[REDACTED:config_denylist]"
    assert redacted["payload"]["vars"]["ok"] == "visible"
    assert redacted["payload"]["nested"][0]["ssh_key"] == "[REDACTED:config_denylist]"
    paths = sorted(f["field_path"] for f in fired)
    assert paths == [
        "payload.nested[0].ssh_key",
        "payload.vars.admin_password",
        "payload.vars.api_token",
    ]
    assert all(f["rule_id"].startswith("denylist:") for f in fired)


def test_denylist_env_secret_true():
    layer = ConfigDenylistLayer(patterns=("*_password",))
    event = {"payload": {"env": [{"name": "token", "secret": True, "value": "v"}]}}
    redacted, fired = layer.redact(event)
    assert redacted["payload"]["env"][0]["value"] == "[REDACTED:config_denylist]"
    assert fired[0]["rule_id"] == "denylist:env.secret-true"
