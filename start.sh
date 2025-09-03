#!/bin/bash

#### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

PROJECT_ROOT="$(realpath "$(dirname "${BASH_SOURCE[0]}")")"
APP_DIR="$PROJECT_ROOT/app"
APP_MODULE="app.main:app"

HOST="0.0.0.0"
PORT="8000"
WORKERS=1

#### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

# move to project + export
cd "$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"

#
# vault pwd
#
export VAULT_PASSWORD_FILE="/tmp/vault/vault_pass.txt"
#export VAULT_PASSWORD="redacted.

#### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

echo ":: start :: $APP_MODULE - $PROJECT_ROOT"
exec uvicorn "$APP_MODULE" \
  --host "$HOST" \
  --port "$PORT" \
  --workers "$WORKERS" \
  --log-level info \
  --reload
