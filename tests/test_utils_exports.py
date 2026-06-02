"""The v0 route layer calls helpers as ``app.utils.<name>`` (e.g.
``utils.resolve_inventory``). An empty ``app/utils/__init__.py`` makes every such
call raise ``AttributeError`` at request time, 500-ing the whole v0 Proxmox
surface (VM list, templates/storage, snapshots, bundles). Pin the re-exports."""

import app.utils as utils


def test_utils_reexports_resolvers_used_by_routes():
    for name in (
        "resolve_inventory",
        "resolve_actions_playbook",
        "resolve_bundles_playbook",
        "resolve_bundles_playbook_init_file",
        "resolve_scenarios_playbook",
    ):
        assert hasattr(utils, name), f"app.utils does not re-export {name}"


def test_utils_exposes_vm_id_name_resolver_submodule():
    # routes reference ``utils.vm_id_name_resolver`` directly
    assert hasattr(utils, "vm_id_name_resolver")
