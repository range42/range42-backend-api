"""Manifest-derived guest preferences passed at Ansible extra-vars precedence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.errors import Range42Error
from app.core.scenario_manifest import validate_vm_manifest
from app.core.scenario_firewall import firewall_runtime_variables


def storage_runtime_variables(scenario_dir: Path) -> dict[str, Any]:
    try:
        path = scenario_dir / 'manifest/scenario_vms.json'
        if not path.resolve().is_relative_to(scenario_dir.resolve()) or path.stat().st_size > 1024 * 1024:
            raise ValueError('Invalid manifest path or size')
        manifest = validate_vm_manifest(json.loads(path.read_text()))
        firewall_variables = firewall_runtime_variables(scenario_dir, manifest)
        if 'guest_preferences_version' not in manifest:
            return firewall_variables
        variables = {
            **firewall_variables,
            'r42_guest_storage': {str(vm['vm_id']): vm['storage'] for vm in manifest['vms']},
            # Evaluated in each imported VM's scope. Ansible removes omit from
            # the module body, so inheritance never reuses a prior VM's pool.
            'proxmox_dest_vm_storage_name': '{{ r42_guest_storage[global_vm_id | string] | default(omit, true) }}',
        }
        if manifest['guest_preferences_version'] == 2:
            variables['r42_guest_cloud_init'] = {str(vm['vm_id']): vm['cloud_init'] for vm in manifest['vms']}
            user = '{{ r42_guest_cloud_init[global_vm_id | string].ssh_user }}'
            dns = '{{ (r42_guest_cloud_init[global_vm_id | string].dns_servers | join(" ")) if r42_guest_cloud_init[global_vm_id | string].dns_servers is not none else omit }}'
            domain = '{{ r42_guest_cloud_init[global_vm_id | string].dns_search_domain | default(omit, true) }}'
            # Protect both the bootstrap aliases and the controller parameters:
            # extra vars override vault and include_role vars respectively.
            variables.update(default_admin_vm_ci_user=user, vm_ci_user=user,
                             global_vm_ci_dns_ips=dns, vm_ci_dns_ips=dns, vm_ci_dns_domain=domain)
        return variables
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise Range42Error(error='invalid_preferences', code='PROJECT_PREFERENCES_INVALID',
                           message='The saved VM preferences are invalid. Review and save the scenario again.') from error
