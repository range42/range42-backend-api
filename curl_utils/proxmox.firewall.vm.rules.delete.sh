#!/bin/bash


curl -X 'DELETE' \
  'http://127.0.0.1:8000/v0/proxmox/firewall/vm/rules/delete' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "as_json": true,
  "proxmox_node": "px-testing",
  "vm_fw_pos": 1,
  "vm_id": "1000"
}' | jq "."
