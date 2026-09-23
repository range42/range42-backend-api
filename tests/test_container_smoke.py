"""Opt-in real image/Compose acceptance; uses only uniquely named disposable state."""
from __future__ import annotations

import json
from contextlib import closing
import os
from pathlib import Path
import secrets
import selectors
import shutil
import subprocess
import time
import uuid

from alembic.script import ScriptDirectory
from cryptography.fernet import Fernet
import httpx
import pytest


IMAGE = os.environ.get("RANGE42_CONTAINER_TEST_IMAGE")
ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not IMAGE, reason="set RANGE42_CONTAINER_TEST_IMAGE to opt into Docker smoke")


@pytest.mark.parametrize("runtime_enabled", [False, True])
def test_authenticated_container_recreation_preserves_state(tmp_path, runtime_enabled):
    name = "r42-packaging-test-" + uuid.uuid4().hex[:10]
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir(mode=0o700)
    token = secrets.token_hex(32)
    for filename, value in (("api-token", token), ("credential-key", Fernet.generate_key().decode())):
        path = secret_dir / filename
        path.write_text(value)
        path.chmod(0o600)
    # Do not use the developer's registry credentials or modify their Docker config.
    client_dir = tmp_path / "docker-client"
    client_dir.mkdir()
    env = {**os.environ, "DOCKER_CONFIG": str(client_dir),
           "RANGE42_CONTAINER_IMAGE": IMAGE,
           "RANGE42_CONTAINER_SECRETS_DIR": str(secret_dir),
           "RANGE42_CONTAINER_PORT": "0", "RANGE42_LISTEN_ADDRESS": "127.0.0.1"}
    command = ["docker", "compose", "-p", name, "-f", str(ROOT / "docker-compose.yml")]
    if runtime_enabled:
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        for relative in ("range42-playbooks/bundles", "range42-ansible_roles-proxmox_controller/roles",
                         "range42-catalog/02_ansible_layer/admin/roles",
                         "range42-catalog/02_ansible_layer/trainee/roles",
                         "range42-catalog/03_container_layer/docker/_ctf", "range42/roles", "collections"):
            (runtime / relative).mkdir(parents=True, exist_ok=True)
        (runtime / "ansible.cfg").write_text("[defaults]\nhost_key_checking = True\n")
        (runtime / "range42/roles/fixture.yml").write_text("[]\n")
        # Public host CA bundle, not a credential or a connection to a live host.
        shutil.copyfile("/etc/ssl/certs/ca-certificates.crt", runtime / "proxmox-ca.pem")
        template = tmp_path / "template"
        template.mkdir(mode=0o700)
        env.update({"RANGE42_RUNTIME_DIR": str(runtime),
                    "RANGE42_WORKSPACE_TEMPLATE_DIR": str(template)})
        command.extend(["-f", str(ROOT / "docker-compose.runtime.yml")])

    def compose(*args):
        result = subprocess.run([*command, *args], env=env, capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def inspect_container():
        container = compose("ps", "-q", "api")
        result = subprocess.run(["docker", "inspect", container], env=env,
                                capture_output=True, text=True, check=True)
        return json.loads(result.stdout)[0]

    def wait_ready():
        address = compose("port", "api", "8000")
        client = httpx.Client(base_url=f"http://{address}", timeout=5, trust_env=False)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                response = client.get("/v1/health")
                if response.status_code == 200:
                    return client
            except httpx.TransportError:
                pass
            time.sleep(0.25)
        client.close()
        raise AssertionError("container did not become live within 45 seconds")

    try:
        invalid = subprocess.run([*command, "run", "--rm", "--no-deps", "-e",
                                  "RANGE42_API_TOKEN_FILE=/run/secrets/not-present", "api"],
                                 env=env, capture_output=True, text=True, timeout=60)
        assert invalid.returncode != 0 and "Cannot read RANGE42_API_TOKEN_FILE" in invalid.stderr
        untouched = "from pathlib import Path; assert not Path('/var/lib/range42/workspaces/.range42.db').exists()"
        compose("run", "--rm", "--no-deps", "--entrypoint", "python", "api", "-c", untouched)
        if runtime_enabled:
            profile_code = """import json
from pathlib import Path
from app.core.bundle_runtime import build_runtime_manifest
components = {name: Path('/runtime') / directory for name, directory in {
    'playbooks': 'range42-playbooks', 'controller': 'range42-ansible_roles-proxmox_controller',
    'catalog': 'range42-catalog', 'range42': 'range42', 'collections': 'collections',
    'ansible_config': 'ansible.cfg'}.items()}
print(json.dumps(build_runtime_manifest(components)))
"""
            profile = compose("run", "--rm", "--no-deps", "--entrypoint", "python", "api", "-c", profile_code)
            assert json.loads(profile)["environment"]["RANGE42_BUNDLE_DIR"] == "/runtime/range42-playbooks/bundles"
            (runtime / "bundle-runtime.json").write_text(profile)
        compose("up", "-d", "--no-build", "--pull", "never")
        with closing(wait_ready()) as client:
            assert client.get("/v1/health").json()["status"] == "ok"
            assert client.get("/docs/openapi.json").status_code == 401
            assert client.get("/v1/health/ready").status_code == 401
            headers = {"Authorization": f"Bearer {token}"}
            ready = client.get("/v1/health/ready", headers=headers)
            assert ready.status_code == 200 and ready.json()["ready"] is True
            assert ready.json()["checks"]["sqlite_wal"]["mode"] == "wal"
            fake_pat = "disposable-pat-" + secrets.token_hex(8)
            response = client.post("/v1/catalog/sources", headers=headers, json={
                "provider": "github", "base_url": "https://github.com", "auth_kind": "pat",
                "token_ref": fake_pat, "repos": [{"owner": "range42", "repo": "range42-catalog"}],
            })
            assert response.status_code == 201
            source_id = response.json()["id"]
            assert response.json()["has_token"] is True and "token_ref" not in response.json()
        inspection = inspect_container()
        assert inspection["HostConfig"]["ReadonlyRootfs"] is True
        assert inspection["HostConfig"]["Init"] is True
        assert inspection["Config"]["User"] == "range42"
        assert not any(mount["Destination"] == "/home/range42/.ssh" for mount in inspection["Mounts"])
        if runtime_enabled:
            for destination in ("/runtime", "/run/range42-template"):
                assert next(mount for mount in inspection["Mounts"]
                            if mount["Destination"] == destination)["RW"] is False
            readonly_probe = """import errno
from pathlib import Path
from app.core.bundle_runtime import runtime_snapshot
profile, fingerprint = runtime_snapshot()
assert len(fingerprint) == 64 and len(profile['components']) == 6
try:
    Path('/runtime/range42/roles/fixture.yml').write_text('changed')
except OSError as exc:
    assert exc.errno == errno.EROFS
else:
    raise AssertionError('runtime must be read-only')
"""
            compose("exec", "-T", "api", "python", "-c", readonly_probe)
        # Verify real writable ControlMaster location and ciphertext without printing it.
        expected_revision = ScriptDirectory(str(ROOT / "alembic")).get_current_head()
        check = """import os, sqlite3, stat, sys
from pathlib import Path
p = Path.home() / '.ssh/range42/container-smoke'
p.write_text('persistent control state')
assert stat.S_IMODE(p.parent.stat().st_mode) == 0o700
db = sqlite3.connect('/var/lib/range42/workspaces/.range42.db')
revision = db.execute('SELECT version_num FROM alembic_version').fetchone()[0]
assert revision == sys.argv[1], (revision, sys.argv[1])
value = db.execute('SELECT token_ref FROM sources LIMIT 1').fetchone()[0]
assert value.startswith('range42:fernet:v1:')
assert os.getuid() != 0
print('validated')
"""
        assert compose("exec", "-T", "api", "python", "-c", check, expected_revision) == "validated"
        agent_probe = """from pathlib import Path
from app.core.ssh_agent import _start_agent
workspace = Path('/var/lib/range42/workspaces/agent-probe')
workspace.mkdir(mode=0o700)
agent = _start_agent(workspace)
socket = Path(agent.env['SSH_AUTH_SOCK'])
try:
    assert socket.exists()
finally:
    agent.close()
assert not socket.exists()
workspace.rmdir()
"""
        compose("exec", "-T", "api", "python", "-c", agent_probe)
        health = inspection["Config"]["Healthcheck"]["Test"]
        assert health[0] == "CMD-SHELL"
        compose("exec", "-T", "api", "sh", "-c", health[1])
        with closing(wait_ready()) as client:
            capability = client.get("/v1/admin/maintenance", headers=headers)
            assert capability.status_code == 200
            proof = capability.json()
            assert proof["protocol"] == "flock-http-intent-v2" and proof["enabled"] is True
            guard = subprocess.Popen([*command, "exec", "-T", "api", "python", "-m", "app.core.maintenance_guard"],
                                     env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
            try:
                guard.stdin.write(json.dumps(proof) + "\n")
                guard.stdin.flush()
                with selectors.DefaultSelector() as selector:
                    selector.register(guard.stdout, selectors.EVENT_READ)
                    assert selector.select(timeout=10), "maintenance helper must acknowledge held locks"
                    acknowledgement = json.loads(guard.stdout.readline())
                assert acknowledgement["status"] == "idle"
                assert guard.poll() is None
                assert client.post("/v0/admin/run/bundles/test/run", headers=headers).status_code == 503
                assert client.post("/v1/catalog/sources/default", headers=headers).status_code == 503
                assert client.get("/v1/health").status_code == 200
                compose("up", "-d", "--no-build", "--pull", "never", "--force-recreate")
            finally:
                guard.stdin.close()
                guard.wait(timeout=15)
                guard.stdout.close()
                guard.stderr.close()
        with closing(wait_ready()) as client:
            ready = client.get("/v1/health/ready", headers=headers)
            assert ready.json()["ready"] is True
            sources = client.get("/v1/catalog/sources", headers=headers).json()["items"]
            assert sources[0]["id"] == source_id and sources[0]["has_token"] is True
            new_proof = client.get("/v1/admin/maintenance", headers=headers).json()
            assert new_proof["lock"] == proof["lock"]
            assert new_proof["process"] != proof["process"]
        assert compose("exec", "-T", "api", "python", "-c",
                       "from pathlib import Path; assert (Path.home()/'.ssh/range42/container-smoke').read_text() == 'persistent control state'") == ""
        assert token not in compose("logs", "--no-color", "api")
        assert fake_pat not in compose("logs", "--no-color", "api")
    finally:
        compose("down", "--volumes", "--remove-orphans", "--timeout", "10")


def test_encrypted_workspace_key_unlock_with_noexec_tmp():
    code = '''
from pathlib import Path
import os, subprocess
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, BestAvailableEncryption
from app.core.ssh_agent import unlock_workspace_keys
w = Path('/var/lib/range42/key-test')
(w/'secrets').mkdir(parents=True, mode=0o700)
(w/'ssh_keys').mkdir(mode=0o700)
password = w/'secrets/vault_pass.txt'
password.write_text('disposable-vault-password')
vault = w/'secrets/default_vault.yml'
vault.write_text('ssh_passphrase_deployer_admin: disposable-key-password\\n')
subprocess.run(['ansible-vault', 'encrypt', '--vault-password-file', str(password), str(vault)],
               check=True, capture_output=True)
key = w/'ssh_keys/r42.test-deployer-key_alice'
key.write_bytes(Ed25519PrivateKey.generate().private_bytes(Encoding.PEM, PrivateFormat.OpenSSH,
               BestAvailableEncryption(b'disposable-key-password')))
key.chmod(0o600)
assert any(line.split()[1] == '/tmp' and 'noexec' in line.split()[3].split(',')
           for line in Path('/proc/mounts').read_text().splitlines())
agent = unlock_workspace_keys(w, password)
assert agent is not None
try:
    result = subprocess.run(['ssh-add', '-l'], env={**os.environ, **agent.env}, capture_output=True, text=True)
    assert result.returncode == 0 and len(result.stdout.splitlines()) == 1, 'Encrypted workspace key was not loaded'
    assert not list(w.rglob('r42-askpass-*')), 'Unlock helper was not removed'
finally:
    agent.close()
assert not list(w.glob('.agent-*')), 'Owned agent directory was not removed'
'''
    result = subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--read-only',
        '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,mode=1777,size=64m',
        '--tmpfs', '/var/lib/range42:rw,exec,nosuid,nodev,uid=1000,gid=1000,mode=700,size=64m',
        '--entrypoint', 'python', IMAGE, '-c', code], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
