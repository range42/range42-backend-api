#!/bin/bash 



curl -X 'POST' \
  'http://127.0.0.1:8000/v0/admin/proxmox/network/node/add' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "proxmox_node": "px-testing",
  "as_json": "true",
  "iface_name": "vmbr142",
  "iface_type": "bridge",
  "bridge_ports": "enp87s0",
  "iface_autostart": 1,
  "ip_address": "192.168.99.2",
  "ip_netmask": "255.255.255.0"
}' | jq "."
