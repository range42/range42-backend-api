"""Utility package surface.

Route modules call these helpers as ``app.utils.<name>`` (e.g.
``utils.resolve_inventory``), so they must be re-exported here. An empty
``__init__`` silently breaks every v0 Proxmox action at request time
(AttributeError -> HTTP 500 on VM list, templates/storage, snapshots, bundles).
"""

from . import vm_id_name_resolver
from .checks_inventory import resolve_inventory
from .checks_playbooks import (
    resolve_actions_playbook,
    resolve_bundles_playbook,
    resolve_bundles_playbook_init_file,
    resolve_scenarios_playbook,
)

__all__ = [
    "vm_id_name_resolver",
    "resolve_inventory",
    "resolve_actions_playbook",
    "resolve_bundles_playbook",
    "resolve_bundles_playbook_init_file",
    "resolve_scenarios_playbook",
]
