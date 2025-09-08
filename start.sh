#!/bin/bash

#### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

PROJECT_ROOT="$(realpath "$(dirname "${BASH_SOURCE[0]}")")"
export PROJECT_ROOT_DIR="$PROJECT_ROOT"
APP_DIR="$PROJECT_ROOT_DIR/app"
APP_MODULE="app.main:app"


export API_BACKEND_PUBLIC_PLAYBOOKS_DIR="$HOME/_products.git-hyde-repo/range42-infrastructures-installers/"
export API_BACKEND_WWWAPP_PLAYBOOKS_DIR="$PROJECT_ROOT_DIR/"
export API_BACKEND_INVENTORY_DIR="$PROJECT_ROOT_DIR/inventory/"
export API_BACKEND_VAULT_FILE="$HOME/_products.git-hyde-repo/range42-ansible_roles-private-devkit//secrets/px-testing.cr42_tailscale.yaml"
#
# vault pwd
#
export VAULT_PASSWORD_FILE="/tmp/vault/vault_pass.txt"
#export VAULT_PASSWORD="redacted.


HOST="0.0.0.0"
PORT="8000"
WORKERS=1

#### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

# move to project + export
cd "$PROJECT_ROOT_DIR" || exit
export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"


#### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

echo ":: start :: $APP_MODULE - $PROJECT_ROOT_DIR"
exec uvicorn "$APP_MODULE" \
  --host "$HOST" \
  --port "$PORT" \
  --workers "$WORKERS" \
  --log-level info  --reload
