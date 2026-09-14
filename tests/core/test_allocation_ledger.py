"""Installed scenarios reserve identifiers even when their guests are absent."""
import json
from pathlib import Path

import pytest

from app.core.allocation_ledger import load_installed_reservations
from app.core.errors import Range42Error


def ledger(tmp_path: Path, rows: list[dict], manifest: dict | None = None):
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (scenarios / "_reserved.json").write_text("".join(json.dumps(row) + "\n" for row in rows))
    if manifest is not None:
        directory = scenarios / "new_scenario" / "manifest"
        directory.mkdir(parents=True)
        (directory / "scenario_vms.json").write_text(json.dumps(manifest))
    return tmp_path


def test_unions_stale_ledger_with_current_manifests_and_all_nics(tmp_path):
    root = ledger(tmp_path, [{"vm_id": 3000, "bridge": "lab1", "ip": "10.42.8.2"}], {
        "vms": [{"vm_id": 3001, "bridge": "lab1", "ip": "10.42.8.3", "nics": [
            {"index": 0, "bridge": "lab1", "ip": "10.42.8.3"},
            {"index": 1, "bridge": "lab2", "ip": "10.42.9.2"},
        ]}], "templates": [{"vm_id": 9901}],
    })
    result = load_installed_reservations(root)
    assert result.vmids == {3000, 3001, 9901}
    assert result.addresses == {("lab1", "10.42.8.2"), ("lab1", "10.42.8.3"), ("lab2", "10.42.9.2")}


def test_duplicate_scenario_and_template_entries_remain_reserved(tmp_path):
    row = {"vm_id": 9901, "bridge": "lab1", "ip": "10.42.8.4/29", "role": "template"}
    result = load_installed_reservations(ledger(tmp_path, [row, row]))
    assert result.vmids == {9901}
    assert result.addresses == {("lab1", "10.42.8.4")}


@pytest.mark.parametrize("row", [{"vm_id": True}, {"vm_id": 3000, "nics": "bad"},
    {"vm_id": 3000, "bridge": "lab1", "ip": "not-an-ip"}])
def test_malformed_reservations_fail_closed(tmp_path, row):
    with pytest.raises(Range42Error, match="installed scenario"):
        load_installed_reservations(ledger(tmp_path, [row]))


def test_missing_ledger_cannot_claim_full_coverage(tmp_path):
    with pytest.raises(Range42Error, match="installed scenario"):
        load_installed_reservations(tmp_path)


def test_rejects_symlinked_manifest_before_reading_outside_runtime(tmp_path):
    root = ledger(tmp_path, [])
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"vms": [{"vm_id": 3001}]}))
    directory = root / "scenarios" / "escape" / "manifest"
    directory.mkdir(parents=True)
    (directory / "scenario_vms.json").symlink_to(outside)
    with pytest.raises(Range42Error, match="installed scenario"):
        load_installed_reservations(root)


def test_rejects_oversized_ledger(tmp_path):
    root = ledger(tmp_path, [])
    (root / "scenarios" / "_reserved.json").write_bytes(b" " * (2 * 1024 * 1024 + 1))
    with pytest.raises(Range42Error, match="installed scenario"):
        load_installed_reservations(root)
