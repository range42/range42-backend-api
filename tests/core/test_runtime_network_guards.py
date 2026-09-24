"""Run generated network guards with real Ansible and a read-only test API."""
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
import json
import os
from pathlib import Path
import signal
import ssl
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
import pytest
import yaml

from tests.core.test_runtime_networks import lifecycle_data, read


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [None, "foreign_alias", "new_attachment", "pending_controller", "interface_order", "interface_address", "parallel_inventory", "guest_config", "node_membership", "http_failure", "invalid_json", "untrusted_ca", "redirect", "cancelled", "fail_first_inventory"])
async def test_real_ansible_refuses_network_mutation_after_review_drift(tmp_path, changed):
    from app.core import runtime_networks
    data = lifecycle_data()
    data["/nodes/pve01/network"] = [
        {"iface": "vmbr11", "cidr": "192.0.2.1/24", "active": 1},
        {"iface": "vmbr12", "cidr": "198.51.100.1/24", "active": 1},
    ]
    if changed in ("parallel_inventory", "guest_config", "fail_first_inventory"):
        count = 48 if changed == "fail_first_inventory" else 24
        data["/cluster/resources"] = [{"vmid": vmid, "node": "pve01", "type": "qemu"} for vmid in range(9100, 9100 + count)]
        data.update({f"/nodes/pve01/qemu/{vmid}/config": {"name": f"guest-{vmid}", "net0": "virtio,bridge=vmbr0", "description": "private-guest-description"} for vmid in range(9100, 9100 + count)})
    observation = await read(tmp_path, data)
    plan = runtime_networks.network_plan({"kind": "sdn_network", "action": "delete", "vnet": "r42blue"}, observation)
    guard = runtime_networks.lifecycle_guard_play(plan)
    if changed == "foreign_alias":
        data["/cluster/sdn/vnets"][0]["alias"] = "range42-deployment-other"
    elif changed == "new_attachment":
        data["/cluster/resources"] = [{"vmid": 9001, "node": "pve01", "type": "lxc"}]
        data["/nodes/pve01/lxc/9001/config"] = {"net0": "bridge=r42blue,name=eth0"}
    elif changed == "pending_controller":
        data["/cluster/sdn/controllers"] = [{"controller": "new", "state": "new"}]
    elif changed == "interface_order":
        data["/nodes/pve01/network"].reverse()
        assert (await read(tmp_path, data))["guards"] == observation["guards"]
    elif changed == "interface_address":
        data["/nodes/pve01/network"][0]["cidr"] = "203.0.113.1/24"
    elif changed == "guest_config":
        data["/nodes/pve01/qemu/9100/config"]["description"] = "changed-private-description"
    elif changed == "node_membership":
        data["/nodes"].append({"node": "pve02"})
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "network-guard-test")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]), False).sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "ca.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))

    activity = {"active": 0, "peak": 0, "paths": []}
    lock = threading.Lock()
    request_started = threading.Event()
    failure_sent = threading.Event()

    class Api(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path.removeprefix("/api2/json")
            with lock:
                activity["active"] += 1
                activity["peak"] = max(activity["peak"], activity["active"])
                activity["paths"].append(self.path.removeprefix("/api2/json"))
            try:
                request_started.set()
                if changed == "cancelled":
                    time.sleep(1)
                if changed == "parallel_inventory":
                    time.sleep(0.08)
                if changed == "fail_first_inventory":
                    # The first queued read is slow; another guard fails before
                    # it finishes. Waiting in input order must not drain the queue.
                    if path != "/cluster/resources":
                        failure_sent.wait(5)
                        time.sleep(1 if path == "/access/permissions" else 0.1)
                body = json.dumps({"data": data.get(path)}).encode()
                status = 200 if path in data else 403
                if self.headers.get("Authorization") != "PVEAPIToken=test@pve!unit=fake":
                    status = 403
                if path == "/nodes" and changed == "http_failure":
                    status = 503
                if path == "/cluster/resources" and changed == "fail_first_inventory":
                    status = 503
                if path == "/nodes" and changed == "invalid_json":
                    body = b"not json"
                if path == "/nodes" and changed == "redirect":
                    status = 302
                try:
                    self.send_response(status)
                    if status == 302:
                        self.send_header("Location", "/api2/json/redirect-target")
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ssl.SSLEOFError):
                    pass
                if changed == "fail_first_inventory" and path == "/cluster/resources":
                    failure_sent.set()
            finally:
                with lock:
                    activity["active"] -= 1
        def log_message(self, *args):
            pass

    class ApiServer(ThreadingHTTPServer):
        request_queue_size = 32

    server = ApiServer(("127.0.0.1", 0), Api)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    marker = tmp_path / "mutation-started"
    plays = [guard, {"hosts": "proxmox", "gather_facts": False, "tasks": [
        {"ansible.builtin.copy": {"content": "changed", "dest": str(marker), "mode": "0600"}},
    ]}]
    playbook = tmp_path / "guard.yml"
    playbook.write_text(yaml.safe_dump(plays, sort_keys=False))
    inventory = tmp_path / "hosts.yml"
    inventory.write_text("proxmox:\n  hosts:\n    local:\n      ansible_connection: local\n      ansible_python_interpreter: '{{ ansible_playbook_python }}'\n")
    variables = tmp_path / "vars.json"
    variables.write_text(json.dumps({"proxmox_api_host": f"127.0.0.1:{server.server_port}", "proxmox_node": "pve01",
                                    "proxmox_api_user": "test@pve", "proxmox_api_token_id": "unit", "proxmox_api_token_secret": "fake"}))
    config = tmp_path / "ansible.cfg"
    config.write_text("[defaults]\nretry_files_enabled=False\n")
    try:
        started = time.monotonic()
        command = [str(Path(sys.executable).parent / "ansible-playbook"), "-i", str(inventory), str(playbook), "-e", f"@{variables}"]
        env = {**os.environ, "ANSIBLE_CONFIG": str(config),
               "ANSIBLE_LIBRARY": str(Path(runtime_networks.__file__).parent / "ansible_modules"),
               "RANGE42_PROXMOX_CA_FILE": "" if changed == "untrusted_ca" else str(cert_path)}
        if changed == "cancelled":
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
            try:
                assert request_started.wait(10), "Verifier did not start"
            finally:
                os.killpg(process.pid, signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=10)
            result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        else:
            result = subprocess.run(command, capture_output=True, text=True, timeout=50, env=env)
        allowed = changed in (None, "interface_order", "parallel_inventory")
        assert (result.returncode == 0) is allowed, result.stdout + result.stderr
        assert marker.exists() is allowed
        assert "private-guest-description" not in result.stdout + result.stderr
        assert "test@pve!unit=fake" not in result.stdout + result.stderr
        assert "/redirect-target" not in activity["paths"]
        if changed == "fail_first_inventory":
            assert len(activity["paths"]) <= 16, "A failed guard drained the queued inventory reads"
        if allowed:
            assert sorted(activity["paths"]) == sorted(row["path"] for row in plan["guards"])
        if changed == "parallel_inventory":
            print(f"Guarded {len(activity['paths'])} identities in {time.monotonic() - started:.3f}s; peak requests {activity['peak']}")
            assert 1 < activity["peak"] <= 8, "Reviewed identities must use bounded parallel reads"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("mixed", [False, True, "destination"])
def test_lifecycle_preserves_other_sources_including_zero_live_rules(tmp_path, mixed):
    from app.core import runtime_networks
    before, after = runtime_networks.preserve_nat_plays({"network": {"subnet": "10.1.0.0/24"},
                                                       "all_subnets": ["10.1.0.0/24", "10.2.0.0/24", "10.3.0.0/24"]})
    rows = [{"snat_source": "10.1.0.0/24", "snat_count": 1}, {"snat_source": "10.2.0.0/24", "snat_count": 3}]
    if mixed is True:
        rows.append({"snat_source": "10.2.0.0/24", "snat_count": 1})
    before["vars"]["r42_native_nat_before"] = {"stdout_lines": [
        "-A POSTROUTING -s 10.2.0.0/24 -o vmbr0 -j SNAT --to-source 192.0.2.1",
        "-A POSTROUTING -s 10.2.0.0/24 -o vmbr0 -j SNAT --to-source " + ("192.0.2.2" if mixed == "destination" else "192.0.2.1"),
    ]}
    role = tmp_path / "roles/range42-ansible_roles-proxmox_controller/tasks"
    role.mkdir(parents=True)
    (role / "main.yml").write_text(yaml.safe_dump([
        {"ansible.builtin.set_fact": {"network_list_snat_rules": rows}, "when": "proxmox_vm_action == 'network_list_snat_rules'"},
        {"ansible.builtin.copy": {"content": "{{ sdn_snat_want }}", "dest": str(tmp_path) + "/{{ sdn_subnet_cidr | replace('/', '-') }}", "mode": "0600"},
         "when": "proxmox_vm_action == 'network_delete_extra_snat_rules'"},
    ]))
    playbook = tmp_path / "play.yml"
    playbook.write_text(yaml.safe_dump([before, after]))
    config = tmp_path / "ansible.cfg"
    config.write_text(f"[defaults]\nroles_path={tmp_path / 'roles'}\nretry_files_enabled=False\n")
    result = subprocess.run([str(Path(sys.executable).parent / "ansible-playbook"), "-i", "proxmox,", "-c", "local", str(playbook)],
                            capture_output=True, text=True, timeout=30, env={**os.environ, "ANSIBLE_CONFIG": str(config)})
    assert (result.returncode == 0) is (not mixed), result.stdout + result.stderr
    assert not (tmp_path / "10.1.0.0-24").exists()
    if not mixed:
        assert (tmp_path / "10.2.0.0-24").read_text() == "3"
        assert (tmp_path / "10.3.0.0-24").read_text() == "0"
