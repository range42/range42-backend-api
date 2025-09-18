#!/bin/bash 


curl -X 'POST' \
  'http://127.0.0.1:8000/v0/admin/proxmox/vms/vm_id/config/vm_set_tag' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "as_json": true,
  "proxmox_node": "px-testing",
  "vm_id": "1000",
  "vm_tag_name": "hello,world"
}' | jq "."
