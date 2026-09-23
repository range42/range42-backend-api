"""Tests for TaintedStringLayer (spec §13 hard invariant - substring scrub).

Catches secret values that leak via stdout/stderr/msg/debug regardless of
which key carried them. Tainted set is built per attempt from cloudinit
vars, vault password, and Source PAT.
"""
import json

import pytest

from app.core.redaction import TaintedStringLayer


def test_redacts_tainted_string_in_stdout():
    layer = TaintedStringLayer(tainted_strings={"my-vault-pass-XYZ"})
    event = {
        "event_data": {
            "res": {
                "stdout": "the password is my-vault-pass-XYZ, do not share",
                "stdout_lines": ["x: my-vault-pass-XYZ"],
            }
        }
    }
    redacted, fired = layer.redact(event)
    assert "my-vault-pass-XYZ" not in str(redacted)
    assert "[REDACTED:tainted_string]" in redacted["event_data"]["res"]["stdout"]
    assert "[REDACTED:tainted_string]" in redacted["event_data"]["res"]["stdout_lines"][0]
    assert len(fired) >= 1
    assert all("rule_id" in f and "field_path" in f for f in fired)


def test_redacts_in_msg_field():
    layer = TaintedStringLayer(tainted_strings={"secret-token-abc"})
    event = {"event_data": {"msg": "auth failed: token=secret-token-abc"}}
    redacted, fired = layer.redact(event)
    assert "secret-token-abc" not in str(redacted)
    assert len(fired) == 1
    assert fired[0]["field_path"] == "event_data.msg"


def test_redacts_in_stderr():
    layer = TaintedStringLayer(tainted_strings={"sekret-yo"})
    event = {"event_data": {"res": {"stderr": "Error: connection refused; sekret-yo failed"}}}
    redacted, fired = layer.redact(event)
    assert "sekret-yo" not in str(redacted)
    assert len(fired) == 1
    assert fired[0]["field_path"] == "event_data.res.stderr"


def test_no_op_when_no_tainted_strings():
    layer = TaintedStringLayer(tainted_strings=set())
    event = {"event_data": {"res": {"stdout": "no secrets here"}}}
    redacted, fired = layer.redact(event)
    assert redacted == event
    assert fired == []


def test_no_match_no_redaction():
    layer = TaintedStringLayer(tainted_strings={"never-appears"})
    event = {"event_data": {"res": {"stdout": "regular output"}}}
    redacted, fired = layer.redact(event)
    assert redacted["event_data"]["res"]["stdout"] == "regular output"
    assert fired == []


def test_handles_nested_dicts_and_lists():
    layer = TaintedStringLayer(tainted_strings={"deep-secret"})
    event = {
        "event_data": {
            "res": {
                "stdout_lines": ["a", "b: deep-secret here", "c"],
                "results": [
                    {"stdout": "deep-secret in nested result"},
                    {"stdout": "clean"},
                ],
            }
        }
    }
    redacted, fired = layer.redact(event)
    assert "deep-secret" not in str(redacted)
    # both nested locations should have fired
    assert len(fired) >= 2
    paths = sorted(f["field_path"] for f in fired)
    assert any("stdout_lines" in p for p in paths)
    assert any("results" in p for p in paths)


def test_skips_empty_tainted_strings():
    """Empty strings in the set should not match (would replace everywhere)."""
    layer = TaintedStringLayer(tainted_strings={"", "real-secret"})
    event = {"event_data": {"res": {"stdout": "a real-secret leak"}}}
    redacted, fired = layer.redact(event)
    assert "real-secret" not in redacted["event_data"]["res"]["stdout"]
    # Empty string should not have triggered any redaction
    assert "[REDACTED:tainted_string]" in redacted["event_data"]["res"]["stdout"]
    # Only one fired entry (for "real-secret") — empty filtered out
    assert len(fired) == 1


def test_rule_id_includes_prefix_for_audit():
    """rule_id should be auditable but not leak the full secret."""
    layer = TaintedStringLayer(tainted_strings={"abcdef-very-long-secret"})
    event = {"event_data": {"res": {"stdout": "x abcdef-very-long-secret y"}}}
    _, fired = layer.redact(event)
    assert len(fired) == 1
    # rule_id should start with tainted_string: but not contain full secret
    assert fired[0]["rule_id"].startswith("tainted_string:")
    assert "abcdef-very-long-secret" not in fired[0]["rule_id"]


def test_tainted_values_are_removed_from_translated_text_headers_and_nested_lists():
    layer = TaintedStringLayer(tainted_strings={"test-api-secret", "test-guest-password"})
    event = {"event_type": "log_line", "payload": {
        "text": "API response debug contains test-api-secret",
        "res": {"invocation": {"module_args": {"headers": {
            "Authorization": "PVEAPIToken=user@pve!test=test-api-secret",
        }}}, "arbitrary_output": [["test-guest-password"], {"value": "test-api-secret"}], "rc": 0},
    }}
    redacted, fired = layer.redact(event)
    assert "test-api-secret" not in str(redacted)
    assert "test-guest-password" not in str(redacted)
    assert redacted["payload"]["res"]["rc"] == 0
    assert {entry["field_path"] for entry in fired} == {
        "payload.text", "payload.res.invocation.module_args.headers.Authorization",
        "payload.res.arbitrary_output[0][0]", "payload.res.arbitrary_output[1].value",
    }


def test_tainted_audit_identifiers_do_not_reveal_secret_prefix():
    layer = TaintedStringLayer(tainted_strings={"abcd-private-value"})
    _, fired = layer.redact({"payload": {"msg": "abcd-private-value"}})
    assert "abcd" not in str(fired)


@pytest.mark.parametrize("ensure_ascii", [True, False])
def test_json_rendered_credential_is_removed_from_translated_stdout(ensure_ascii):
    secret = 'credential-{{literal}}-line\nslash\\-quote"-café'
    rendered = json.dumps({"debug_value": secret}, ensure_ascii=ensure_ascii)
    event = {"event_type": "log_line", "payload": {"text": rendered}}

    redacted, fired = TaintedStringLayer({secret}).redact(event)

    assert redacted["payload"]["text"] == '{"debug_value": "[REDACTED:tainted_string]"}'
    assert fired == [{"rule_id": "tainted_string:known-secret", "field_path": "payload.text"}]
    assert event["payload"]["text"] == rendered


def test_non_string_values_pass_through():
    layer = TaintedStringLayer(tainted_strings={"x"})
    event = {"event_data": {"res": {"stdout": None, "rc": 0, "changed": False}}}
    redacted, fired = layer.redact(event)
    assert redacted["event_data"]["res"]["stdout"] is None
    assert redacted["event_data"]["res"]["rc"] == 0
    assert fired == []


def test_does_not_mutate_original_event():
    layer = TaintedStringLayer(tainted_strings={"leak"})
    event = {"event_data": {"res": {"stdout": "a leak occurred"}}}
    original_stdout = event["event_data"]["res"]["stdout"]
    redacted, _ = layer.redact(event)
    # Original event must remain untouched
    assert event["event_data"]["res"]["stdout"] == original_stdout
    assert "leak" not in redacted["event_data"]["res"]["stdout"]
