#!/bin/bash 


curl -X 'POST' \
  'http://127.0.0.1:8000/v0/admin/proxmox/vms/vm_id/create' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "proxmox_node": "px-testing",
  "vm_cores": 1,
  "vm_cpu": "host",
  "vm_disk_size": 42,
  "vm_id": "6000",
  "vm_iso": "local:iso/ubuntu-24.04.2-live-server-amd64.iso",
  "vm_memory": 2042,
  "vm_name": "TESTS-new-vm",
  "vm_sockets": 1
}' | jq "." 
