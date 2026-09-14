"""The catalog readiness role must honor the runner's Ansible inventory."""
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


def test_guest_readiness_uses_inventory_connection_instead_of_ssh_alias(tmp_path):
    task = Path(__file__).resolve().parents[3] / "range42-catalog/02_ansible_layer/admin/roles/ansible.utils/tasks/wait/openssh_server/is_reachable.yml"
    ansible = Path(sys.executable).parent / "ansible-playbook"
    if not task.exists() or not ansible.exists():
        pytest.skip("requires the sibling catalog and ansible-playbook")
    inventory = tmp_path / "hosts.yml"
    inventory.write_text(yaml.safe_dump({"all": {"hosts": {"guest_alias.invalid": {
        "ansible_connection": "local", "ansible_python_interpreter": sys.executable,
        "ARG_vm_ssh_name": "guest_alias.invalid", "deployer_cli_user_ssh_known_hosts": str(tmp_path / "known_hosts"),
    }}}}))
    playbook = tmp_path / "main.yml"
    playbook.write_text(yaml.safe_dump([{"hosts": "guest_alias.invalid", "gather_facts": False,
                                      "tasks": [{"ansible.builtin.include_tasks": str(task)}]}]))
    config = tmp_path / "ansible.cfg"
    config.write_text("[defaults]\nretry_files_enabled = False\n")
    process = subprocess.Popen([str(ansible), "-i", str(inventory), str(playbook)],
                               env={**os.environ, "ANSIBLE_CONFIG": str(config)},
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        output, _ = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        output, _ = process.communicate()
        pytest.fail(f"Readiness ignored the local inventory connection and retried raw SSH:\n{output}")
    assert process.returncode == 0, output
