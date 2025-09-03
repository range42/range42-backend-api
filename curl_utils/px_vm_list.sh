#!/bin/bash

curl -sS -X POST 'http://localhost:8000/api/proxmox/list' \
  -H 'Content-Type: application/json' \
  -d '{"proxmox_node":"px-testing", "as_json":true }'


#
#
#curl -sS -X POST 'http://localhost:8000/api/proxmox/list' \
#  -H 'Content-Type: application/json' \
#  -d '{"proxmox_node":"px-testing", "as_json":false }'
#


#### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

#
# debug mode - (disabled)
#

#curl -sS -X POST 'http://localhost:8000/api/proxmox/list' \
#  -H 'Content-Type: application/json' \
#  -d '{"proxmox_node":"px-testing", "as_json":false",  "vault_password_file":"/tmp/vault/vault_pass.txt"}'
#
#
