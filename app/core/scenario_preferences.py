"""Manifest-derived clone preferences passed at Ansible extra-vars precedence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.errors import Range42Error
from app.core.scenario_manifest import validate_vm_manifest


def storage_runtime_variables(scenario_dir: Path) -> dict[str, Any]:
    try:
        path = scenario_dir / 'manifest/scenario_vms.json'
        if not path.resolve().is_relative_to(scenario_dir.resolve()) or path.stat().st_size > 1024 * 1024:
            raise ValueError('Invalid manifest path or size')
        manifest = validate_vm_manifest(json.loads(path.read_text()))
        if 'guest_preferences_version' not in manifest:
            return {}
        return {
            'r42_guest_storage': {str(vm['vm_id']): vm['storage'] for vm in manifest['vms']},
            # Evaluated in each imported VM's scope. Ansible removes omit from
            # the module body, so inheritance never reuses a prior VM's pool.
            'proxmox_dest_vm_storage_name': '{{ r42_guest_storage[global_vm_id | string] | default(omit, true) }}',
        }
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise Range42Error(error='invalid_preferences', code='PROJECT_PREFERENCES_INVALID',
                           message='The saved VM storage preferences are invalid. Review and save the scenario again.') from error
