"""Tests for redaction audit writer (spec §18.3)."""
import json

from app.core.redaction import RedactionAuditWriter


def test_audit_writer_appends_schema(tmp_path):
    a = RedactionAuditWriter(tmp_path / "redactions.jsonl")
    a.record(
        deployment_id="dep-1",
        attempt_id="att-1",
        layer="config_denylist",
        rule_id="denylist:password-suffix",
        event_seq=42,
        field_path="tasks[3].vars.admin_password",
    )
    lines = (tmp_path / "redactions.jsonl").read_text().splitlines()
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert set(obj) == {
        "ts",
        "deployment_id",
        "attempt_id",
        "layer",
        "rule_id",
        "event_seq",
        "field_path",
    }
    assert obj["ts"].endswith("Z")
    assert obj["layer"] == "config_denylist"
