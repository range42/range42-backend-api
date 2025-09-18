#!/bin/bash 


curl -X 'POST' \
  'http://127.0.0.1:8000/v0/admin/proxmox/network/vm/delete' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "as_json": true,
  "proxmox_node": "px-testing",
  "storage_name": "local",
  "vm_id": "1001",
  "vm_vmnet_id": 2
}' | jq "."  

