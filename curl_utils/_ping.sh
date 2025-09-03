#!/bin/bash 


 curl -s -X POST http://localhost:8000/api/ping \
  -H 'Content-Type: application/json' \
  -d '{"hosts":"r42_vuln_box_group"}' | \
  jq '.log_plain'

