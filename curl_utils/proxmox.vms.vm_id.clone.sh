#!/bin/bash 


curl -X 'POST' \
  'http://127.0.0.1:8000/v0/proxmox/vms/vm_id/clone' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "proxmox_node": "px-testing",
  "vm_description": "my description",
  "vm_id": "6000",
  "vm_name": "test-cloned",
  "vm_new_id": "6002"
}' | jq "." 
