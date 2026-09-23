"""Verified TLS for every Proxmox HTTP client, including private lab CAs."""
from __future__ import annotations

import ssl

from app.core.config import Settings


def proxmox_verify() -> ssl.SSLContext | bool:
    ca_file = Settings().proxmox_ca_file
    if not ca_file:
        return True
    try:
        return ssl.create_default_context(cafile=ca_file)
    except (OSError, ssl.SSLError) as exc:
        raise RuntimeError("Cannot load RANGE42_PROXMOX_CA_FILE; configure the trusted Proxmox CA certificate") from exc
