#!/bin/bash 


curl -X 'POST' \
  'http://127.0.0.1:8000/v0/proxmox/storage/download_iso' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "as_json": true,
  "iso_file_content_type": "iso",
  "iso_file_name": "ubuntu-24.04-live-server-amd64.iso",
  "iso_url": "https://releases.ubuntu.com/24.04/ubuntu-24.04-live-server-amd64.iso",
  "proxmox_node": "px-testing",
  "proxmox_storage": "local"
}' | jq "." 
