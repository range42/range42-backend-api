#!/bin/bash


curl -X 'POST' \
  'http://127.0.0.1:8000/v0/proxmox/firewall/datacenter/disable' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "as_json": true,
  "proxmox_api_host": "127.0.0.1:18007",
  "proxmox_node": "px-testing"
}' # | jq "."