#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(realpath "$(dirname "${BASH_SOURCE[0]}")")"
export PROJECT_ROOT_DIR="$PROJECT_ROOT"

# Load .env file if it exists
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a
    source "$PROJECT_ROOT/.env"
    set +a
fi

# Install Ansible collections if needed
if [ ! -d "$HOME/.ansible/collections/ansible_collections/community/general" ]; then
    echo ":: Installing Ansible collections..."
    ansible-galaxy collection install -r requirements.yml -p ~/.ansible/collections
fi

# Set defaults for required vars
export API_BACKEND_WWWAPP_PLAYBOOKS_DIR="${API_BACKEND_WWWAPP_PLAYBOOKS_DIR:-$PROJECT_ROOT_DIR/}"
export API_BACKEND_INVENTORY_DIR="${API_BACKEND_INVENTORY_DIR:-$PROJECT_ROOT_DIR/inventory/}"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

cd "$PROJECT_ROOT_DIR" || exit
export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"

echo ":: start :: app.main:app - $PROJECT_ROOT_DIR"
exec uvicorn app.main:app \
    --host "$HOST" \
    --port "$PORT" \
    --log-level info \
    --reload
