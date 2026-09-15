#!/bin/bash
set -eu

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    printf '%s\n' \
        'Uso: bash start-order-flow-websocket.sh' \
        'Inicia el WebSocket de Order Flow en primer plano (puerto predeterminado: 8766).' \
        'Requiere el entorno .venv, NATS y el Engine Order Flow existentes.' \
        'Lee app/order_flow_export/.env y MARKETBOT_ORDER_FLOW_WS_*; pide el token si falta.' \
        'MARKETBOT_PROJECT_ROOT permite seleccionar otro checkout.' \
        'Ctrl+C detiene el WebSocket. Ngrok se inicia por separado.'
    exit 0
fi
if [ "$#" -ne 0 ]; then
    printf '%s\n' 'Argumento desconocido. Usa --help.' >&2
    exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="${MARKETBOT_PROJECT_ROOT:-$script_dir}"
cd "$project_dir"
project_dir="$(pwd)"
python="$project_dir/.venv/bin/python"
if [ ! -x "$python" ]; then
    printf '%s\n' 'No se encontro .venv/bin/python. Usa el checkout Linux con su entorno preparado.' >&2
    exit 1
fi
export PYTHONDONTWRITEBYTECODE=1

# Read settings through MarketBot; never source .env as shell code or print secrets.
configuration="$("$python" -B -c '
import sys
from app.integration.order_flow_websocket import load_order_flow_websocket_settings
try:
    settings = load_order_flow_websocket_settings()
except Exception:
    print("Revisa app/order_flow_export/.env, .env y las variables MARKETBOT_*.", file=sys.stderr)
    sys.exit(2)
has_token = bool(settings.order_flow_ws_token and settings.order_flow_ws_token.get_secret_value().strip())
print("configured" if has_token else "missing", settings.order_flow_ws_port)
')"
read -r token_status service_port <<< "$configuration"

if command -v ss >/dev/null 2>&1; then
    listeners="$(ss -ltnH "sport = :$service_port")"
    if [ -n "$listeners" ]; then
        printf 'El puerto %s ya esta ocupado. Revisa el servicio actual antes de iniciar otro.\n' \
            "$service_port" >&2
        exit 1
    fi
fi

if [ "$token_status" = "missing" ]; then
    if ! read -r -s -p 'Token del WebSocket (entrada oculta): ' MARKETBOT_ORDER_FLOW_WS_TOKEN; then
        printf '\nNo se pudo leer el token. Configura MARKETBOT_ORDER_FLOW_WS_TOKEN.\n' >&2
        exit 1
    fi
    printf '\n'
    if [[ -z "${MARKETBOT_ORDER_FLOW_WS_TOKEN//[[:space:]]/}" ]]; then
        printf '%s\n' 'El token no puede estar vacio.' >&2
        exit 1
    fi
    export MARKETBOT_ORDER_FLOW_WS_TOKEN
fi

printf 'Iniciando Order Flow WebSocket en puerto %s. Ctrl+C para detener.\n' "$service_port"
exec "$python" -B -c 'from app.operator_cli.main import app; app()' serve order-flow
