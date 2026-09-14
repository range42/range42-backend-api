"""Execute the actual Ansible ownership guard against a local verified TLS API."""
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
import pytest
import yaml

from app.core.runtime_runner import _ownership_guard


@pytest.mark.parametrize("case", ["owned", "foreign", "template", "unauthorized", "untrusted"])
def test_composite_cannot_start_before_verified_tls_and_exact_ownership(tmp_path, case):
    binary = Path(sys.executable).parent / "ansible-playbook"
    if not binary.is_file():
        pytest.skip("ansible-playbook is not installed")
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "runtime-test")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "ca.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    key_path.chmod(0o600)

    class Api(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/api2/json/nodes/pve01/qemu/3191/config"
            assert self.headers["Authorization"] == "PVEAPIToken=test@pve!unit=fake-secret"
            body = json.dumps({"data": {"name": "owned-guest", "template": int(case == "template"),
                              "description": "range42-deployment:other" if case == "foreign" else "range42-deployment:dep"}}).encode()
            self.send_response(401 if case == "unauthorized" else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Api)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    marker = tmp_path / "composite-started"
    playbook = tmp_path / "guard.yml"
    playbook.write_text(yaml.safe_dump([
        _ownership_guard([{"vm_id": 3191, "vm_name": "owned-guest"}], "dep"),
        {"hosts": "proxmox", "gather_facts": False, "tasks": [{"ansible.builtin.copy": {
            "content": "composite started", "dest": str(marker), "mode": "0600",
        }}]},
    ], sort_keys=False))
    inventory = tmp_path / "hosts.yml"
    inventory.write_text("proxmox:\n  hosts:\n    local:\n      ansible_connection: local\n      ansible_python_interpreter: '{{ ansible_playbook_python }}'\n")
    variables = tmp_path / "vars.json"
    variables.write_text(json.dumps({"proxmox_api_host": f"127.0.0.1:{server.server_port}", "proxmox_node": "pve01",
                                    "proxmox_api_user": "test@pve", "proxmox_api_token_id": "unit",
                                    "proxmox_api_token_secret": "fake-secret", "r42_deployment_id": "dep"}))
    config = tmp_path / "ansible.cfg"
    config.write_text("[defaults]\nretry_files_enabled = False\n")
    env = {**os.environ, "ANSIBLE_CONFIG": str(config), "RANGE42_PROXMOX_CA_FILE": "" if case == "untrusted" else str(cert_path)}
    try:
        result = subprocess.run([str(binary), "-i", str(inventory), str(playbook), "-e", f"@{variables}"],
                                capture_output=True, text=True, env=env, timeout=30)
        assert (result.returncode == 0) is (case == "owned"), result.stdout + result.stderr
        assert marker.exists() is (case == "owned")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("matching_node", [False, True])
async def test_snat_wrapper_checks_actual_ssh_node_before_composite(tmp_path, monkeypatch, matching_node):
    from types import SimpleNamespace
    import socket
    from app.core import runtime_runner
    binary = Path(sys.executable).parent / "ansible-playbook"
    if not binary.is_file():
        pytest.skip("ansible-playbook is not installed")
    marker = tmp_path / "nat-composite-started"
    bundles = tmp_path / "bundles"
    bundle = bundles / "proxmox/sdn_network.internet_on/main.yml"
    bundle.parent.mkdir(parents=True)
    bundle.write_text(yaml.safe_dump([{"hosts": "proxmox", "gather_facts": False, "tasks": [{
        "ansible.builtin.copy": {"content": "would apply SDN", "dest": str(marker), "mode": "0600"},
    }]}]))
    monkeypatch.setenv("RANGE42_BUNDLE_DIR", str(bundles))
    async def verified(*args):
        return {"vmids": [], "missing_vmids": [], "variables": {}, "bundle": "proxmox/sdn_network.internet_on", "subnet": "10.42.70.0/24"}
    monkeypatch.setattr(runtime_runner, "_verified_plan", verified)
    monkeypatch.setattr(runtime_runner, "runtime_targets", lambda path: ([], None))
    artifact = tmp_path / "runner/attempt"
    scenario = artifact / "checkout/scenarios/test"
    scenario.mkdir(parents=True)
    deployment = SimpleNamespace(id="dep", scenario_label="test", workspace_path=str(tmp_path))
    attempt = SimpleNamespace(operation={"request": {"kind": "sdn_snat", "enabled": True}})
    run = await runtime_runner.prepare_runtime_run(deployment, attempt, None, SimpleNamespace(playbook=scenario / "main.yml"), artifact)
    inventory = tmp_path / "test-hosts.yml"
    inventory.write_text(yaml.safe_dump({"all": {"vars": {"ansible_connection": "local", "ansible_python_interpreter": str(Path(sys.executable))},
        "children": {"proxmox": {"hosts": {"api": {}}}, "proxmox_cli": {"hosts": {"r42-proxmox-cli": {}}}}}}))
    variables = tmp_path / "vars.json"
    variables.write_text(json.dumps({"proxmox_node": socket.gethostname().split(".")[0] if matching_node else "different-proxmox-node"}))
    config = tmp_path / "ansible.cfg"
    config.write_text("[defaults]\nretry_files_enabled=False\n")
    result = subprocess.run([str(binary), "-i", str(inventory), str(run.playbook), "-e", f"@{variables}"],
        env={**os.environ, "ANSIBLE_CONFIG": str(config)}, capture_output=True, text=True, timeout=30)
    assert (result.returncode == 0) is matching_node, result.stdout + result.stderr
    assert marker.exists() is matching_node
