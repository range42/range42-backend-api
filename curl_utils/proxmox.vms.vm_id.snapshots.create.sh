#!/bin/bash 


curl -X 'POST' \
  'http://127.0.0.1:8000/v0/proxmox/vms/vm_id/snapshot/create' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "as_json": true,
  "proxmox_node": "px-testing",
  "vm_id": "1000",
  "vm_snapshot_description": "MY_DESCRIPTION",
  "vm_snapshot_name": "MY_VM_SNAPSHOT"
}' | jq "." 
