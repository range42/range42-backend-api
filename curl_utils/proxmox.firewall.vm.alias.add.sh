#!/bin/bash


curl -X 'POST' \
  'http://127.0.0.1:8000/v0/proxmox/firewall/vm/alias/add' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "as_json": true,
  "proxmox_node": "px-testing",
  "vm_fw_alias_cidr": "192.168.123.0/24",
  "vm_fw_alias_comment": "this_comment",
  "vm_fw_alias_name": "test",
  "vm_id": "1000"
}' | jq "."
