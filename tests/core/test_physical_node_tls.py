"""Real HTTPS proves the selected peer; no live Proxmox request is sent."""

from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
from ipaddress import ip_address
import json
import os
from pathlib import Path
import ssl
import threading
from types import SimpleNamespace

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID
import pytest

from app.core.errors import Range42Error
from app.core.physical_nodes import NodeAttestation
from app.core.preparation_transport import verify_api_peer
from tests.core.test_physical_nodes import proof


@pytest.fixture
def endpoint(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-node.localhost")]
    )
    now = datetime.now(timezone.utc)
    certificate = (
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
    pem = tmp_path / "pve-ssl.pem"
    pem.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
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
        certificate.public_bytes(serialization.Encoding.DER)
    ).hexdigest()
    source = os.environ.get("RANGE42_PREPARATION_AGENT_SOURCE")
    if source:
        path = Path(source) / "roles/range42_preparation_agent/files/agent.py"
        spec = importlib.util.spec_from_file_location(
            "actual_preparation_agent_tls", path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert (
            module.api_certificate_fingerprint(tmp_path, owner_uid=os.geteuid())
            == fingerprint
        )
    payload = {
        "document": {"data": {"version": "synthetic-pve-version"}},
        "certificate_info": {
            "data": [
                {
                    "filename": "pve-ssl.pem",
                    "fingerprint": ":".join(
                        fingerprint[i : i + 2] for i in range(0, 64, 2)
                    ).upper(),
                }
            ]
        },
    }
    observations = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            observations.append(self.path)
            assert (
                self.headers["Authorization"]
                == "PVEAPIToken=synthetic@pve!key=PRIVATE-SYNTHETIC"
            )
            assert self.path in [
                "/api2/json/version",
                "/api2/json/nodes/pve-test/certificates/info",
            ]
            raw = json.dumps(
                payload["certificate_info"]
                if self.path.endswith("/certificates/info")
                else payload["document"]
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(pem, private)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv("RANGE42_PROXMOX_CA_FILE", str(pem))
    host = SimpleNamespace(
        api_url=f"https://127.0.0.1:{server.server_port}",
        token_ref="synthetic@pve!key=PRIVATE-SYNTHETIC",
    )
    try:
        yield host, fingerprint, payload, observations
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


@pytest.mark.asyncio
async def test_actual_verified_tls_peer_matches_the_root_observed_leaf(endpoint):
    host, fingerprint, _, observations = endpoint
    await verify_api_peer(
        host, NodeAttestation(**proof(api_certificate_sha256=fingerprint))
    )
    assert observations == [
        "/api2/json/version",
        "/api2/json/nodes/pve-test/certificates/info",
    ]


@pytest.mark.asyncio
async def test_another_https_leaf_never_binds_the_selected_node(endpoint):
    host, _, _, _ = endpoint
    with pytest.raises(Range42Error) as error:
        await verify_api_peer(
            host, NodeAttestation(**proof(api_certificate_sha256="0" * 64))
        )
    assert error.value.code == "PHYSICAL_NODE_ENDPOINT_MISMATCH"


@pytest.mark.asyncio
async def test_malformed_https_envelope_is_an_actionable_refusal(endpoint):
    host, fingerprint, payload, _ = endpoint
    payload["document"] = []
    with pytest.raises(Range42Error) as error:
        await verify_api_peer(
            host, NodeAttestation(**proof(api_certificate_sha256=fingerprint))
        )
    assert error.value.code == "PHYSICAL_NODE_ENDPOINT_MISMATCH"


@pytest.mark.asyncio
async def test_untrusted_tls_is_refused_without_an_insecure_fallback(
    endpoint, monkeypatch
):
    host, fingerprint, _, observations = endpoint
    monkeypatch.delenv("RANGE42_PROXMOX_CA_FILE")
    with pytest.raises(Range42Error):
        await verify_api_peer(
            host, NodeAttestation(**proof(api_certificate_sha256=fingerprint))
        )
    assert not observations


@pytest.mark.asyncio
async def test_missing_configured_ca_is_an_actionable_unavailable_capability(
    endpoint, monkeypatch, tmp_path
):
    host, fingerprint, _, _ = endpoint
    monkeypatch.setenv("RANGE42_PROXMOX_CA_FILE", str(tmp_path / "missing-ca.pem"))
    with pytest.raises(Range42Error) as error:
        await verify_api_peer(
            host, NodeAttestation(**proof(api_certificate_sha256=fingerprint))
        )
    assert error.value.code == "PREPARATION_AGENT_UNAVAILABLE"


@pytest.mark.asyncio
async def test_shared_transport_leaf_cannot_hide_a_different_generated_node_certificate(
    endpoint,
):
    host, fingerprint, payload, _ = endpoint
    payload["certificate_info"]["data"][0]["fingerprint"] = "0" * 64
    with pytest.raises(Range42Error) as error:
        await verify_api_peer(
            host, NodeAttestation(**proof(api_certificate_sha256=fingerprint))
        )
    assert error.value.code == "PHYSICAL_NODE_ENDPOINT_MISMATCH"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"filename": "pveproxy-ssl.pem", "fingerprint": "e" * 64}],
        [{"filename": "pve-ssl.pem", "fingerprint": "e" * 64}] * 2,
        [{"filename": "pve-ssl.pem", "fingerprint": "SHA1:invalid"}],
    ],
)
async def test_generated_node_certificate_is_mandatory_unambiguous_and_sha256(
    endpoint, rows
):
    host, fingerprint, payload, _ = endpoint
    payload["certificate_info"]["data"] = rows
    with pytest.raises(Range42Error):
        await verify_api_peer(
            host, NodeAttestation(**proof(api_certificate_sha256=fingerprint))
        )


@pytest.mark.asyncio
async def test_shared_proxy_leaf_accepts_only_the_exact_generated_node_witness(
    endpoint,
):
    host, fingerprint, payload, observations = endpoint
    payload["certificate_info"]["data"][0]["fingerprint"] = "f" * 64
    await verify_api_peer(
        host,
        NodeAttestation(
            **proof(
                api_certificate_sha256=fingerprint, node_certificate_sha256="f" * 64
            )
        ),
    )
    assert observations == [
        "/api2/json/version",
        "/api2/json/nodes/pve-test/certificates/info",
    ]
