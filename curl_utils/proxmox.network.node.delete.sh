#!/bin/bash 



curl -X 'POST' \
  'http://127.0.0.1:8000/v0/proxmox/network/node/delete' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{   "as_json": true,
  "iface_name": "vmbr142",
  "proxmox_node": "px-testing",
  "storage_name": "local" }'  | jq "."
