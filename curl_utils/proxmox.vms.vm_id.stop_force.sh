#!/bin/bash





curl -X 'POST' \
  'http://127.0.0.1:8000/v0/proxmox/vms/vm_id/stop_force' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "proxmox_node": "px-testing",
  "vm_id": "1000"
}' | jq "."