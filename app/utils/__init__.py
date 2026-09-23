"""Utility package surface.

Route modules call the path resolvers as ``app.utils.<name>`` (e.g.
``utils.resolve_inventory``), so they must be re-exported here. An empty
``__init__`` silently breaks every v0 Proxmox action at request time
(AttributeError -> HTTP 500 on VM list, templates/storage, snapshots, bundles).

Only the path resolvers are re-exported. ``checks_inventory`` / ``checks_playbooks``
import nothing heavier than ``app.core.logging``, so this is cycle-free.
``vm_id_name_resolver`` is intentionally NOT re-exported: it imports
``app.core.runner`` (circular at startup), and every consumer already imports it
directly via ``from app.utils.vm_id_name_resolver import resolv_id_to_vm_name``.
"""

from .checks_inventory import resolve_inventory
from .checks_playbooks import (
    resolve_actions_playbook,
    resolve_bundles_playbook,
    resolve_bundles_playbook_init_file,
    resolve_scenarios_playbook,
)

__all__ = [
    "resolve_inventory",
    "resolve_actions_playbook",
    "resolve_bundles_playbook",
    "resolve_bundles_playbook_init_file",
    "resolve_scenarios_playbook",
]
