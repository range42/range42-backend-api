#!/bin/bash


curl -X 'POST' \
  'http://127.0.0.1:8000/v0/admin/proxmox/firewall/vm/enable' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "as_json": true,
  "proxmox_node": "px-testing",
  "vm_fw_pos": 1,
  "vm_id": "1000",
  "vm_name": "test"
}' | jq "."
