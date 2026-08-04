#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
export UV_PROJECT_ENVIRONMENT="$PROJECT_ROOT/.venv-linux"

export MARKETBOT_SEC_USER_AGENT="MarketBot/0.1 lgonzalez.ar@gmail.com"

cd "$PROJECT_ROOT"
exec uv run marketbot engine peter-lynch
