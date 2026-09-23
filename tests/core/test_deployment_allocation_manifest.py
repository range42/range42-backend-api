"""Ownership parser handles supported legacy manifests without opaque failures."""
import json

import pytest

from app.core.deployment_allocations import manifest_assignments
from app.core.errors import Range42Error


@pytest.mark.parametrize("bad_nic", [None, "bad", 3, [], True])
def test_legacy_nic_entries_require_objects(tmp_path, bad_nic):
    directory = tmp_path / "manifest"
    directory.mkdir()
    (directory / "scenario_vms.json").write_text(json.dumps({
        "version": 1, "vms": [{"vm_id": 5000, "nics": [bad_nic]}],
    }))
    with pytest.raises(Range42Error) as raised:
        manifest_assignments(tmp_path)
    assert raised.value.code == "ALLOCATION_MANIFEST_INVALID"
