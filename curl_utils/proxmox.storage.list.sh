#!/bin/bash 


curl -X 'POST' \
  'http://127.0.0.1:8000/v0/admin/proxmox/storage/list' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "as_json": true,
  "proxmox_node": "px-testing",
  "storage_name": "local"
}' | jq "." 
