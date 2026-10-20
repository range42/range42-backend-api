"""Actual verified HTTPS corroborates original receipts without a live VM write."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
import json
import ssl
import threading
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID
import pytest

from app.core.errors import Range42Error
from app.core.models import ProxmoxHost
from tests.core import test_workshop_ownership_import as ownership

env = ownership.env


@pytest.fixture
def endpoint(tmp_path, monkeypatch):
    from app.core.workshop_ownership import digest

    key = Ed25519PrivateKey.generate()
    name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-original.localhost")]
    )
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(minutes=10))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, None)
    )
    pem = tmp_path / "original.pem"
    pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private = tmp_path / "key.pem"
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private.chmod(0o600)
    fingerprint = hashlib.sha256(
        cert.public_bytes(serialization.Encoding.DER)
    ).hexdigest()
    witness = ownership.proof()
    witness["api_certificate_sha256"] = witness["node_certificate_sha256"] = fingerprint
    documents = {}
    for row in witness["assignments"]:
        view = {
            "name": row["vm_name"],
            "description": row["generation"]["owner"],
            "vmgenid": row["generation"]["vmgenid"],
            "cpu": "host",
            "cores": 2,
            "memory": 4096,
            "net0": "virtio=02:11:22:33:44:55,bridge=" + row["nics"][0]["bridge"],
            "ipconfig0": "ip=" + row["nics"][0]["ip"] + "/28",
            "scsi0": f"ceph:vm-{row['vm_id']}-disk-0,size=32G",
        }
        row["generation"]["configuration_sha256"] = digest(view)
        documents[f"/api2/json/nodes/pve42-1/qemu/{row['vm_id']}/config"] = {
            "data": {**view, "cipassword": "PRIVATE-MUST-NOT-RETURN"}
        }
    documents["/api2/json/version"] = {"data": {"version": "synthetic"}}
    documents["/api2/json/nodes/pve42-1/certificates/info"] = {
        "data": [{"filename": "pve-ssl.pem", "fingerprint": fingerprint}]
    }
    witness["review_revision"] = digest(
        {k: v for k, v in witness.items() if k not in {"review_revision", "handoff"}}
    )
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            assert (
                self.headers["Authorization"] == "PVEAPIToken=synthetic@pve!key=PRIVATE"
            )
            path = urlsplit(self.path).path
            assert path in documents
            body = deepcopy(documents[path])
            if urlsplit(self.path).query == "current=0" and body.get("pending_drift"):
                body["data"]["cores"] = 99
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(pem, private)

    class TLSHTTPServer(ThreadingHTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(2)
            return context.wrap_socket(connection, server_side=True), address

    server = TLSHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("RANGE42_PROXMOX_CA_FILE", str(pem))
    try:
        yield f"https://127.0.0.1:{server.server_port}", witness, documents, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


async def review(env, endpoint, monkeypatch, *, paused=True):
    from app.core.workshop_ownership_transport import observe
    from app.routes.v1.admin import workshop

    url, witness, _, _ = endpoint

    async def rpc(request):
        if request["action"] == "admission":
            return {
                "scope": "legacy_controller_queue",
                "state": "paused" if paused else "open",
                "queued_count": 0,
                "running_count": 0,
            }
        assert request == {"action": "ownership"}
        return witness

    monkeypatch.setattr(workshop, "agent_request", rpc)
    async with env[1]() as session:
        host = await session.get(ProxmoxHost, "host")
        host.api_url = url
        await session.commit()
        return await observe(session, "cohort")


@pytest.mark.asyncio
async def test_real_tls_checks_every_current_and_configured_guest_before_review(
    env, endpoint, monkeypatch
):
    result = await review(env, endpoint, monkeypatch)
    assert len(result.witness.assignments) == 10
    assert len(endpoint[3]) == 22
    assert all(
        path.endswith("/version")
        or path.endswith("/certificates/info")
        or "/config?current=" in path
        for path in endpoint[3]
    )
    assert "PRIVATE" not in result.witness.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "https_leaf",
        "node_certificate",
        "generation",
        "pending",
        "extra_nic",
        "extra_disk",
    ],
)
async def test_changed_native_generation_or_endpoint_cannot_be_reviewed(
    env, endpoint, monkeypatch, damage
):
    from app.core.workshop_ownership import digest

    _, witness, documents, _ = endpoint
    first = documents["/api2/json/nodes/pve42-1/qemu/43010/config"]
    if damage == "https_leaf":
        witness["api_certificate_sha256"] = "0" * 64
    elif damage == "node_certificate":
        documents["/api2/json/nodes/pve42-1/certificates/info"]["data"][0][
            "fingerprint"
        ] = "0" * 64
    elif damage == "generation":
        first["data"]["vmgenid"] = "99999999-2222-4333-8444-555555555555"
    elif damage == "pending":
        first["pending_drift"] = True
    elif damage == "extra_nic":
        first["data"]["net1"] = "virtio=02:AA:BB:CC:DD:EE,bridge=vmbr999"
    elif damage == "extra_disk":
        first["data"]["efidisk0"] = "ceph:foreign"
    witness["review_revision"] = digest(
        {k: v for k, v in witness.items() if k not in {"review_revision", "handoff"}}
    )
    with pytest.raises(Range42Error):
        await review(env, endpoint, monkeypatch)


@pytest.mark.asyncio
async def test_unpaused_queue_refuses_before_any_proxmox_observation(
    env, endpoint, monkeypatch
):
    with pytest.raises(Range42Error) as error:
        await review(env, endpoint, monkeypatch, paused=False)
    assert error.value.code == "WORKSHOP_OWNERSHIP_PAUSE_REQUIRED"
    assert endpoint[3] == []


@pytest.mark.asyncio
async def test_a_refused_guest_cancels_and_joins_other_observations_before_return(
    endpoint,
):
    import asyncio
    from types import SimpleNamespace
    from app.core.workshop_ownership import OwnershipWitness
    from app.core.workshop_ownership_transport import verify_guest_configurations

    url, witness, documents, _ = endpoint
    documents["/api2/json/nodes/pve42-1/qemu/43010/config"]["data"]["cores"] = 99
    before = asyncio.all_tasks()
    try:
        with pytest.raises(Range42Error):
            await verify_guest_configurations(
                SimpleNamespace(api_url=url, token_ref="synthetic@pve!key=PRIVATE"),
                OwnershipWitness.model_validate(witness),
            )
        remaining = asyncio.all_tasks() - before
        assert not remaining, (
            "A failed review left concurrent observations using a closed HTTP client"
        )
    finally:
        pending = asyncio.all_tasks() - before
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
