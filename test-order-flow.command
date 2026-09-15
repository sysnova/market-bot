#!/bin/bash
set -eu

client_dir="$(cd "$(dirname "$0")" && pwd)"
client_script="$client_dir/example_client.py"
if [ ! -f "$client_script" ]; then
    client_script="$client_dir/app/order_flow_export/example_client.py"
fi
if [ ! -f "$client_script" ]; then
    printf '%s\n' 'No se encontro example_client.py. Extrae el ZIP completo.' >&2
    exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
    printf '%s\n' 'Falta uv. Si usas Homebrew: brew install uv' >&2
    printf '%s\n' 'O visita https://docs.astral.sh/uv/getting-started/installation/' >&2
    exit 1
fi

# Run only the portable client, without installing the MarketBot project.
exec uv run --no-project --no-config --python 3.14 \
    --with 'websockets>=16,<17' "$client_script" "$@"
