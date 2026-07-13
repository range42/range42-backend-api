"""Render the deployer-cli lifecycle scripts for a generated scenario.

The EXISTING deployer-cli (``range42-context``, which we cannot change) resolves the
active scenario dir and execs ``<scenario>.setup.sh`` where ``<scenario>`` is the
directory basename -- so the script filenames are a hard contract keyed on
``spec.name``. Each renderer reproduces the exact structure of the real ``demo_lab``
reference scripts (the ``:?`` vault guard, ``"$@"`` forwarding, the jq ``mapfile``
loop over devkit binaries on ``$PATH``); only the scenario name is templated in.

Paths inside the scripts are RELATIVE to the scenario dir, which the CLI sets as cwd
(``./main.yml``, ``./manifest/scenario_vms.json``). The module is pure: spec in,
text out, no IO.
"""

from __future__ import annotations

from app.core.scenario_renderer.types import ScenarioSpec

# main playbooks (relative to the scenario dir) targeted by the setup scripts
_MAIN_PLAYBOOK = "./main.yml"
_MAIN_VMS_ONLY_PLAYBOOK = "./main_vms_only.yml"

# the ansible-playbook invocation shared by setup / setup_vms_only / reset. The `:?`
# guard on the vault var fails loudly if `range42-context use <codename> <scenario>`
# was not run first. Trailing "$@" forwards the TUI feature-flag args.
_VAULT_GUARD = (
    '"${RANGE42_VAULT_PASSWORD_FILE:?RANGE42_VAULT_PASSWORD_FILE is not set '
    "— run: range42-context use <codename> <scenario>}\""
)


def _ansible_playbook_block(playbook: str) -> str:
    return (
        'ansible-playbook -i "${RANGE42_ANSIBLE_ROLES__INVENTORY_DIR}/inventory_default.yml" \\\n'
        '\t-l "all" \\\n'
        f"\t\"{playbook}\" --vault-password-file {_VAULT_GUARD} \\\n"
        '\t"$@"\n'
    )


def render_setup_sh(spec: ScenarioSpec) -> str:
    """``<name>.setup.sh`` -- full deploy against ``./main.yml``, forwarding ``"$@"``."""
    return (
        "#!/bin/bash\n\n"
        "##\n"
        '## Trailing "$@" propagates any extra args to ansible-playbook.\n'
        "## Typical use : feature flag overrides from the TUI, e.g.\n"
        f"##   {spec.name}.setup.sh -e INSTALL_WAZUH=NO -e INSTALL_MISP=YES\n"
        "## See ./manifest/feature_flags.yml for the list of toggleable features.\n"
        "##\n\n"
        f"{_ansible_playbook_block(_MAIN_PLAYBOOK)}"
    )


def render_setup_vms_only_sh(spec: ScenarioSpec) -> str:
    """``<name>.setup_vms_only.sh`` -- deploy skipping templates (``./main_vms_only.yml``)."""
    return (
        "#!/bin/bash\n\n"
        "##\n"
        "## deploy VMs only — skip template download and creation\n"
        "## faster redeploy when templates already exist on proxmox\n"
        "##\n"
        '## Trailing "$@" propagates any extra args to ansible-playbook.\n'
        "## Typical use : feature flag overrides from the TUI, e.g.\n"
        f"##   {spec.name}.setup_vms_only.sh -e INSTALL_WAZUH=NO\n"
        "## See ./manifest/feature_flags.yml for the list of toggleable features.\n"
        "##\n\n"
        f"{_ansible_playbook_block(_MAIN_VMS_ONLY_PLAYBOOK)}"
    )


def _manifest_preamble() -> str:
    """Resolve + guard the manifest path (relative to the scenario dir / cwd)."""
    return (
        'SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        'MANIFEST="$SCRIPT_DIR/manifest/scenario_vms.json"\n\n'
        'if [[ ! -f "$MANIFEST" ]]; then\n'
        '    echo "ERROR: manifest not found: $MANIFEST" >&2\n'
        "    exit 1\n"
        "fi\n\n"
    )


def _delete_loop(id_regex_source: str) -> str:
    """The shared stop_force + delete loop over the devkit binaries on ``$PATH``.

    ``id_regex_source`` names the array whose members form the vm_id filter regex
    (``ALL_IDS`` for delete_all, ``SCENARIO_VM_IDS`` for VMs-only / reset). Copied
    verbatim from the reference: list VMs once, filter by vm_id, pipe to stop_force
    then delete, then scrub each IP from known_hosts.
    """
    return (
        f"ID_REGEX=$(printf '|%s' \"${{{id_regex_source}[@]}}\" | sed 's/^|//')\n\n"
        'VM_LIST_JSON=$(proxmox_vm.list.to.jsons.sh 2>&1 | grep \'"vm_id":[0-9]\')\n'
        'if [ -z "$VM_LIST_JSON" ]; then\n'
        '    echo "ERROR: proxmox_vm.list.to.jsons.sh returned no VM data (no vm_id lines) — aborting" >&2\n'
        '    printf "output: %.200s\\n" "$VM_LIST_JSON" >&2\n'
        "    exit 1\n"
        "fi\n"
        'echo "$VM_LIST_JSON" | jq -c | grep -E "\\"vm_id\\":($ID_REGEX)([^0-9]|\\$)" | proxmox_vm.vm_id.stop_force.to.jsons.sh\n'
        'echo "$VM_LIST_JSON" | jq -c | grep -E "\\"vm_id\\":($ID_REGEX)([^0-9]|\\$)" | proxmox_vm.vm_id.delete.to.jsons.sh\n\n'
        'for ip in "${INFRASTRUCTURE_IP[@]}"; do\n'
        '    echo ":: REMOVE SSH KEY FOR : $ip"\n'
        '    ssh-keygen -f "$HOME/.ssh/known_hosts" -R "$ip"\n'
        "done\n"
    )


def render_delete_all_sh(spec: ScenarioSpec) -> str:
    """``<name>.delete_all.sh`` -- delete this scenario's VMs AND its templates."""
    return (
        "#!/bin/bash\n\n"
        "##\n"
        f"## delete all — VMs + templates of THIS scenario ({spec.name})\n"
        "##\n"
        "## VM IDs are read from the scenario manifest:\n"
        "##   manifest/scenario_vms.json\n"
        "##\n"
        "## ⚠ WARNING ⚠\n"
        "##   Templates (9xxx) are deleted by this script. They may be shared with other\n"
        "##   scenarios deployed on the same Proxmox. Run this only when you're sure no\n"
        "##   other scenario relies on them, or run delete_vms_only.sh instead to keep them.\n"
        "##\n\n"
        f"{_manifest_preamble()}"
        "# extract VM IDs + template IDs + IPs from the manifest\n"
        "mapfile -t SCENARIO_VM_IDS  < <(jq -r '.vms[].vm_id'        \"$MANIFEST\")\n"
        "mapfile -t TEMPLATE_VM_IDS  < <(jq -r '.templates[].vm_id'  \"$MANIFEST\")\n"
        "mapfile -t INFRASTRUCTURE_IP < <(jq -r '.vms[].ip'          \"$MANIFEST\")\n\n"
        'ALL_IDS=("${SCENARIO_VM_IDS[@]}" "${TEMPLATE_VM_IDS[@]}")\n\n'
        'echo ":: stopping and deleting VMs + templates"\n'
        'echo ":: scenario VMs: ${SCENARIO_VM_IDS[*]}"\n'
        'echo ":: templates   : ${TEMPLATE_VM_IDS[*]}"\n'
        'echo ""\n\n'
        f"{_delete_loop('ALL_IDS')}"
        '\necho ""\n'
        'echo ":: done — VMs and templates removed"\n'
        'echo ":: redeploy from scratch with: range42-context deploy"\n'
        'echo ""\n'
    )


def render_delete_vms_only_sh(spec: ScenarioSpec) -> str:
    """``<name>.delete_vms_only.sh`` -- delete only ``.vms[]``, keep templates."""
    return (
        "#!/bin/bash\n\n"
        "##\n"
        "## delete VMs only — scenario VMs (filter by vm_id), keep templates\n"
        "## faster redeploy: skip template bootstrap (templates already exist)\n"
        "##\n"
        "## VM IDs are read from the scenario manifest:\n"
        "##   manifest/scenario_vms.json\n"
        "##\n\n"
        f"{_manifest_preamble()}"
        "# extract VM IDs + IPs from the manifest (templates kept untouched)\n"
        "mapfile -t SCENARIO_VM_IDS  < <(jq -r '.vms[].vm_id' \"$MANIFEST\")\n"
        "mapfile -t INFRASTRUCTURE_IP < <(jq -r '.vms[].ip'   \"$MANIFEST\")\n\n"
        'echo ":: stopping and deleting scenario VMs (keeping templates)..."\n'
        'echo ":: scenario VMs: ${SCENARIO_VM_IDS[*]}"\n'
        'echo ""\n\n'
        f"{_delete_loop('SCENARIO_VM_IDS')}"
        '\necho ""\n'
        'echo ":: done — templates preserved"\n'
        'echo ":: redeploy with: range42-context deploy"\n'
        'echo ""\n'
    )


def render_reset_setup_sh(spec: ScenarioSpec) -> str:
    """``<name>.reset.setup.sh`` -- delete this scenario's VMs then re-run the deploy."""
    return (
        "#!/bin/bash\n\n"
        "##\n"
        "## reset — delete this scenario's VMs (filter by vm_id from manifest) then re-deploy\n"
        "##\n\n"
        f"{_manifest_preamble()}"
        "mapfile -t SCENARIO_VM_IDS  < <(jq -r '.vms[].vm_id' \"$MANIFEST\")\n"
        "mapfile -t INFRASTRUCTURE_IP < <(jq -r '.vms[].ip'   \"$MANIFEST\")\n\n"
        'echo ":: stopping and deleting scenario VMs (vm_ids: ${SCENARIO_VM_IDS[*]})..."\n\n'
        f"{_delete_loop('SCENARIO_VM_IDS')}"
        "\n##\n"
        '## Trailing "$@" propagates any extra args to ansible-playbook.\n'
        "## Typical use : feature flag overrides from the TUI, e.g.\n"
        f"##   {spec.name}.reset.setup.sh -e INSTALL_WAZUH=NO\n"
        "## See ./manifest/feature_flags.yml for the list of toggleable features.\n"
        "##\n\n"
        f"{_ansible_playbook_block(_MAIN_PLAYBOOK)}"
    )


def render_scripts(spec: ScenarioSpec) -> dict[str, str]:
    """Return the five mandatory lifecycle scripts keyed on the contract filenames.

    The writer consumes this mapping directly: each key is ``<spec.name>.<verb>.sh``,
    the exact name the deployer-cli execs.
    """
    return {
        f"{spec.name}.setup.sh": render_setup_sh(spec),
        f"{spec.name}.setup_vms_only.sh": render_setup_vms_only_sh(spec),
        f"{spec.name}.delete_all.sh": render_delete_all_sh(spec),
        f"{spec.name}.delete_vms_only.sh": render_delete_vms_only_sh(spec),
        f"{spec.name}.reset.setup.sh": render_reset_setup_sh(spec),
    }
