#!/bin/bash



curl -X 'POST' \
  'http://127.0.0.1:8000/v0/proxmox/firewall/vm/rules/apply' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "as_json": false,
  "proxmox_node": "px-node-01",
  "vm_fw_action": "ACCEPT",
  "vm_fw_comment": "Test comment",
  "vm_fw_dest": "0.0.0.0/0",
  "vm_fw_dport": "22",
  "vm_fw_enable": 1,
  "vm_fw_iface": "net0",
  "vm_fw_log": "debug",
  "vm_fw_pos": 5,
  "vm_fw_proto": "tcp",
  "vm_fw_source": "192.168.1.0/24",
  "vm_fw_sport": "1024",
  "vm_fw_type": "in",
  "vm_id": "1000"
}' | jq "."
