#!/bin/bash 


curl -X 'POST' \
  'http://127.0.0.1:8000/v0/proxmox/network/vm/add' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
    "proxmox_node": "px-testing",
    "as_json": true,
    "iface_bridge": "vmbr142",
    "iface_model": "virtio",
    "vm_id": "1001",
    "vm_vmnet_id": "1"      }' | jq "."
