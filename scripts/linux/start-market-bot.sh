#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
export UV_PROJECT_ENVIRONMENT="$PROJECT_ROOT/.venv-linux"
DEFINITION_PATH="$PROJECT_ROOT/configs/marketbot/7.47.0.yaml"
MARKETBOT_EXECUTABLE="$UV_PROJECT_ENVIRONMENT/bin/marketbot"
SCRIPT_PATH="$PROJECT_ROOT/scripts/linux/start-market-bot.sh"
ROLE="launcher"
RUNTIME_ROOT="$PROJECT_ROOT/.runtime"
SYMBOLS=""
NO_BELL=0
DETACH=0
READY_TIMEOUT=1800
SESSION="marketbot"
STOCK_ANALYZER_ENV="${MARKETBOT_STOCK_ANALYZER_ENV:-$PROJECT_ROOT/../stock-analyzer/apps/alert-runner/.env}"
LOG_MAX_BYTES="${MARKETBOT_LOG_MAX_BYTES:-52428800}"
LOG_BACKUP_COUNT="${MARKETBOT_LOG_BACKUP_COUNT:-3}"
LOG_ROTATION_INTERVAL_SECONDS="${MARKETBOT_LOG_ROTATION_INTERVAL_SECONDS:-60}"
MANUAL_START_PROCESSES=()

load_shared_openai_key() {
  [[ -z "${MARKETBOT_OPENAI_API_KEY:-}" && -f "$STOCK_ANALYZER_ENV" ]] || return 0
  local line value
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" =~ ^[[:space:]]*OPENAI_API_KEY[[:space:]]*=(.*)$ ]]; then
      value="${BASH_REMATCH[1]%$'\r'}"
      value="${value#\"}"
      value="${value%\"}"
      value="${value#\'}"
      value="${value%\'}"
      if [[ -n "$value" ]]; then
        export MARKETBOT_OPENAI_API_KEY="$value"
      fi
      return 0
    fi
  done < "$STOCK_ANALYZER_ENV"
}

load_shared_openai_key

usage() {
  cat <<'EOF'
Usage: ./scripts/linux/start-market-bot.sh [options]

Options:
  --symbols AAPL,MSFT   Override the PostgreSQL universe for this run.
  --runtime-root PATH   Runtime directory (default: .runtime).
  --definition-path PATH
                        Explicit immutable MarketBot definition (default: 7.47.0).
  --no-bell             Disable alert bells.
  --detach              Create the tmux runtime without attaching a client.
  --ready-timeout SEC   Readiness timeout (default: 1800).
  --session NAME        tmux session name (default: marketbot).
  -h, --help            Show this help.

Manual components (run from the repository root):
  ./scripts/linux/start-market-bot.sh --role order-flow
EOF
}

while (($#)); do
  case "$1" in
    --role) ROLE="$2"; shift 2 ;;
    --symbols) SYMBOLS="$2"; shift 2 ;;
    --runtime-root) RUNTIME_ROOT="$2"; shift 2 ;;
    --definition-path) DEFINITION_PATH="$2"; shift 2 ;;
    --no-bell) NO_BELL=1; shift ;;
    --detach) DETACH=1; shift ;;
    --ready-timeout) READY_TIMEOUT="$2"; shift 2 ;;
    --session) SESSION="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$RUNTIME_ROOT" != /* ]]; then
  RUNTIME_ROOT="$PROJECT_ROOT/$RUNTIME_ROOT"
fi
if [[ "$DEFINITION_PATH" != /* ]]; then
  DEFINITION_PATH="$PROJECT_ROOT/$DEFINITION_PATH"
fi
export MARKETBOT_DEFINITION_PATH="$DEFINITION_PATH"

STATUS_ROOT="$RUNTIME_ROOT/status"
LOG_ROOT="$RUNTIME_ROOT/logs"
PLAN_PATH="$STATUS_ROOT/runtime-process-plan.json"

prepare_runtime() {
  [[ -f "$DEFINITION_PATH" ]] || {
    echo "MarketBot definition not found: $DEFINITION_PATH" >&2
    return 1
  }
  (cd "$PROJECT_ROOT" && uv sync --frozen)
  [[ -x "$MARKETBOT_EXECUTABLE" ]] || {
    echo "MarketBot executable was not installed in $UV_PROJECT_ENVIRONMENT." >&2
    return 1
  }
}

write_runtime_plan() {
  local args=(run marketbot runtime-plan --runtime-root "$RUNTIME_ROOT" --no-bell)
  [[ -n "$SYMBOLS" ]] && args+=(--symbols "$SYMBOLS")
  mkdir -p "$STATUS_ROOT"
  (cd "$PROJECT_ROOT" && uv "${args[@]}") >"$PLAN_PATH.tmp"
  mv -f "$PLAN_PATH.tmp" "$PLAN_PATH"
}

ensure_runtime_plan() {
  [[ -s "$PLAN_PATH" ]] || write_runtime_plan
}

engine_is_active() {
  local slot="$1"
  ensure_runtime_plan
  (cd "$PROJECT_ROOT" && uv run python -c \
    'import json,sys; plan=json.load(open(sys.argv[1], encoding="utf-8")); raise SystemExit(0 if sys.argv[2] in plan["active_engine_slots"] else 1)' \
    "$PLAN_PATH" "$slot")
}

process_starts_manually() {
  local name="$1"
  local manual_name
  for manual_name in "${MANUAL_START_PROCESSES[@]}"; do
    [[ "$name" == "$manual_name" ]] && return 0
  done
  return 1
}

plan_startup_batches() {
  (cd "$PROJECT_ROOT" && uv run python -c \
    'import json,sys; plan=json.load(open(sys.argv[1], encoding="utf-8")); print("\n".join("\t".join(batch) for batch in plan["startup_batches"]))' \
    "$PLAN_PATH")
}

plan_process_arguments() {
  local name="$1"
  (cd "$PROJECT_ROOT" && uv run python -c \
    'import json,sys; plan=json.load(open(sys.argv[1], encoding="utf-8")); process=next(item for item in plan["processes"] if item["name"] == sys.argv[2]); sys.stdout.buffer.write(b"".join(arg.encode("utf-8") + b"\0" for arg in process["arguments"]))' \
    "$PLAN_PATH" "$name")
}

plan_ready_paths() {
  (cd "$PROJECT_ROOT" && uv run python -c \
    'import json,sys; plan=json.load(open(sys.argv[1], encoding="utf-8")); names=set(sys.argv[2:]); sys.stdout.buffer.write(b"".join(item["ready_path"].encode("utf-8") + b"\0" for item in plan["processes"] if item["name"] in names and item["ready_path"] is not None))' \
    "$PLAN_PATH" "$@")
}

plan_all_ready_paths() {
  (cd "$PROJECT_ROOT" && uv run python -c \
    'import json,sys; plan=json.load(open(sys.argv[1], encoding="utf-8")); sys.stdout.buffer.write(b"".join(item["ready_path"].encode("utf-8") + b"\0" for item in plan["processes"] if item["ready_path"] is not None))' \
    "$PLAN_PATH")
}

runtime_matches_plan() {
  (cd "$PROJECT_ROOT" && uv run python - "$PLAN_PATH" "${MANUAL_START_PROCESSES[@]}" <<'PY'
import json
import sys
from pathlib import Path

plan = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
manual_processes = set(sys.argv[2:])
version = plan["definition_version"]
for process in plan["processes"]:
    if process["name"] in manual_processes:
        continue
    ready_path = process.get("ready_path")
    if ready_path is None:
        continue
    path = Path(ready_path)
    if not path.is_file():
        raise SystemExit(1)
    try:
        ready = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SystemExit(1) from None
    if ready.get("marketbot_definition_version") != version:
        raise SystemExit(1)
PY
  )
}

clear_runtime_readiness() {
  mkdir -p "$STATUS_ROOT"
  local -a all_ready_paths=()
  mapfile -d '' -t all_ready_paths < <(plan_all_ready_paths)
  ((${#all_ready_paths[@]} == 0)) || rm -f -- "${all_ready_paths[@]}"
  rm -f "$STATUS_ROOT"/{entry-opportunity-monitor,order-flow-monitor,long-portfolio-monitor,news-monitor,4hgeri-monitor,swing-trade-monitor,patreon-caps-analysis,patreon-caps-alerts,elliott-wave-analysis,support-confirmation-analysis,signal-fusion-analysis,signal-fusion-buys}.ready.json
}

exec_marketbot() {
  if (($# < 2)) || [[ "$1" != "run" || "$2" != "marketbot" ]]; then
    echo "Invalid MarketBot command: $*" >&2
    exit 2
  fi
  shift 2
  exec "$MARKETBOT_EXECUTABLE" "$@"
}

run_manual_plan_process() {
  local name="$1"
  local -a process_arguments=()
  ensure_runtime_plan
  mapfile -d '' -t process_arguments < <(plan_process_arguments "$name")
  cd "$PROJECT_ROOT"
  exec_marketbot "${process_arguments[@]}"
}

run_analysis() {
  cd "$PROJECT_ROOT"
  wait_ready "$STATUS_ROOT/alert.ready.json"
  touch "$LOG_ROOT/alert.out.log"
  exec tail -n 200 -F "$LOG_ROOT/alert.out.log"
}

run_confirmed() {
  local args=(run marketbot alerts confirmed \
    --ready-path "$STATUS_ROOT/confirmed-buy-monitor.ready.json")
  ((NO_BELL)) && args+=(--no-bell)
  cd "$PROJECT_ROOT"
  exec_marketbot "${args[@]}"
}

run_opportunities() {
  cd "$PROJECT_ROOT"
  exec_marketbot run marketbot monitor entry-opportunity \
    --ready-path "$STATUS_ROOT/entry-opportunity-monitor.ready.json"
}

validate_order_flow_readiness() {
  "$UV_PROJECT_ENVIRONMENT/bin/python" - \
    "$DEFINITION_PATH" "$STATUS_ROOT/order-flow.ready.json" <<'PY'
import json
import sys
from pathlib import Path

import yaml

definition_path = Path(sys.argv[1])
ready_path = Path(sys.argv[2])
definition = yaml.safe_load(definition_path.read_text(encoding="utf-8"))
ready = json.loads(ready_path.read_text(encoding="utf-8"))
spec = definition["engines"]["order-flow"]
expected = {
    "marketbot_definition_version": definition["version"],
    "engine_implementation": spec["implementation"],
    "engine_strategy_version": spec["strategy"]["version"],
}
mismatches = {
    key: {"expected": value, "actual": ready.get(key)}
    for key, value in expected.items()
    if ready.get(key) != value
}
if mismatches:
    print("Order Flow readiness does not match the selected definition:", file=sys.stderr)
    print(json.dumps(mismatches, indent=2, sort_keys=True), file=sys.stderr)
    raise SystemExit(1)
PY
}

run_order_flow_monitor() {
  cd "$PROJECT_ROOT"
  printf '\033[2J\033[H'
  echo 'ORDER FLOW | INICIANDO'
  echo
  echo 'Esperando que el engine Order Flow publique readiness...'
  echo 'Luego se mostraran ASTS/ASTX/ASTN/NBIS/NBIZ al recibir eventos de mercado.'
  wait_ready "$STATUS_ROOT/order-flow.ready.json"
  validate_order_flow_readiness
  exec_marketbot run marketbot monitor order-flow \
    --ready-path "$STATUS_ROOT/order-flow-monitor.ready.json"
}

run_news() {
  cd "$PROJECT_ROOT"
  exec_marketbot run marketbot monitor news \
    --ready-path "$STATUS_ROOT/news-monitor.ready.json"
}

run_long_portfolio_monitor() {
  local args=(run marketbot alerts long-portfolio \
    --ready-path "$STATUS_ROOT/long-portfolio-monitor.ready.json")
  ((NO_BELL)) && args+=(--no-bell)
  cd "$PROJECT_ROOT"
  exec_marketbot "${args[@]}"
}

run_4hgeri() {
  cd "$PROJECT_ROOT"
  wait_ready "$STATUS_ROOT/4hgeri.ready.json"
  exec_marketbot run marketbot monitor 4hgeri \
    --ready-path "$STATUS_ROOT/4hgeri-monitor.ready.json"
}

run_swing_trade() {
  cd "$PROJECT_ROOT"
  wait_ready "$STATUS_ROOT/swing-trade.ready.json"
  exec_marketbot run marketbot monitor swing-trade \
    --ready-path "$STATUS_ROOT/swing-trade-monitor.ready.json"
}

run_patreon_caps_analysis() {
  cd "$PROJECT_ROOT"
  wait_ready "$STATUS_ROOT/patreon-caps-v1.ready.json"
  exec_marketbot run marketbot monitor patreon-caps \
    --ready-path "$STATUS_ROOT/patreon-caps-analysis.ready.json"
}

run_patreon_caps_alerts() {
  local args=(run marketbot alerts patreon-caps \
    --ready-path "$STATUS_ROOT/patreon-caps-alerts.ready.json")
  ((NO_BELL)) && args+=(--no-bell)
  cd "$PROJECT_ROOT"
  exec_marketbot "${args[@]}"
}

run_elliott_wave() {
  cd "$PROJECT_ROOT"
  wait_ready "$STATUS_ROOT/elliott-wave-v0.ready.json"
  exec_marketbot run marketbot monitor elliott-wave \
    --ready-path "$STATUS_ROOT/elliott-wave-analysis.ready.json"
}

run_support_confirmation() {
  cd "$PROJECT_ROOT"
  wait_ready "$STATUS_ROOT/support-confirmation-v0.ready.json"
  local monitor_args=(run marketbot monitor support-confirmation \
    --ready-path "$STATUS_ROOT/support-confirmation-analysis.ready.json")
  ((NO_BELL)) && monitor_args+=(--no-bell)
  exec_marketbot "${monitor_args[@]}"
}

run_signal_fusion_analysis() {
  cd "$PROJECT_ROOT"
  wait_ready "$STATUS_ROOT/signal-fusion-v0.ready.json"
  exec_marketbot run marketbot monitor signal-fusion --mode analysis --no-bell \
    --ready-path "$STATUS_ROOT/signal-fusion-analysis.ready.json"
}

run_signal_fusion_buys() {
  cd "$PROJECT_ROOT"
  mkdir -p "$STATUS_ROOT"
  rm -f "$STATUS_ROOT/signal-fusion-buys.ready.json"
  local args=(run marketbot monitor signal-fusion --mode buys \
    --ready-path "$STATUS_ROOT/signal-fusion-buys.ready.json")
  ((NO_BELL)) && args+=(--no-bell)
  exec_marketbot "${args[@]}"
}

wait_ready() {
  local deadline=$((SECONDS + READY_TIMEOUT)) missing path
  while :; do
    if declare -F check_children >/dev/null; then
      check_children || return 1
    fi
    missing=0
    for path in "$@"; do
      [[ -f "$path" ]] || missing=1
    done
    ((missing == 0)) && return
    if ((SECONDS >= deadline)); then
      echo "Timed out waiting for readiness files:" >&2
      printf '  %s\n' "$@" >&2
      return 1
    fi
    sleep 0.5
  done
}

run_control() {
  cd "$PROJECT_ROOT"
  # EXIT traps run after Bash has unwound function-local variables. Keep the
  # child registry in shell scope so cleanup can still terminate every process
  # group when startup fails.
  MARKETBOT_CHILD_PIDS=()
  MARKETBOT_CHILD_NAMES=()
  LOG_ROTATOR_PID=""

  validate_log_rotation_settings() {
    local name value
    for name in LOG_MAX_BYTES LOG_BACKUP_COUNT LOG_ROTATION_INTERVAL_SECONDS; do
      value="${!name}"
      if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
        echo "$name must be a positive integer, got: $value" >&2
        return 2
      fi
    done
  }

  rotate_runtime_logs() {
    local path size index source target
    local -a paths=("$LOG_ROOT"/*.out.log "$LOG_ROOT"/*.err.log)
    for path in "${paths[@]}"; do
      [[ -f "$path" ]] || continue
      size="$(stat -c %s -- "$path")"
      ((size > LOG_MAX_BYTES)) || continue
      rm -f -- "$path.$LOG_BACKUP_COUNT"
      for ((index = LOG_BACKUP_COUNT - 1; index >= 1; index--)); do
        source="$path.$index"
        target="$path.$((index + 1))"
        [[ -f "$source" ]] && mv -f -- "$source" "$target"
      done
      tail -c "$LOG_MAX_BYTES" -- "$path" >"$path.1.tmp"
      mv -f -- "$path.1.tmp" "$path.1"
      : >"$path"
    done
  }

  log_rotation_loop() {
    while :; do
      sleep "$LOG_ROTATION_INTERVAL_SECONDS"
      rotate_runtime_logs
    done
  }

  cleanup() {
    trap - EXIT INT TERM
    echo
    echo "Stopping every MarketBot process..."
    if [[ -n "$LOG_ROTATOR_PID" ]]; then
      kill -TERM "$LOG_ROTATOR_PID" 2>/dev/null || true
      wait "$LOG_ROTATOR_PID" 2>/dev/null || true
    fi
    for pid in "${MARKETBOT_CHILD_PIDS[@]}"; do
      kill -TERM -- "-$pid" 2>/dev/null || true
      kill -TERM "$pid" 2>/dev/null || true
    done

    local deadline=$((SECONDS + 8)) running pid
    while ((SECONDS < deadline)); do
      running=0
      for pid in "${MARKETBOT_CHILD_PIDS[@]}"; do
        kill -0 "$pid" 2>/dev/null && running=1
      done
      ((running == 0)) && break
      sleep 0.2
    done

    for pid in "${MARKETBOT_CHILD_PIDS[@]}"; do
      kill -KILL -- "-$pid" 2>/dev/null || true
      kill -KILL "$pid" 2>/dev/null || true
    done
    if [[ -n "${TMUX_PANE:-}" ]]; then
      tmux kill-session -t "$SESSION" 2>/dev/null || true
    fi
  }
  trap cleanup EXIT
  trap 'exit 130' INT TERM
  trap 'exit 129' HUP

  start_background() {
    local name="$1"; shift
    if (($# < 2)) || [[ "$1" != "run" || "$2" != "marketbot" ]]; then
      echo "Invalid MarketBot process command for $name: $*" >&2
      return 2
    fi
    setsid "$MARKETBOT_EXECUTABLE" "${@:3}" \
      >>"$LOG_ROOT/$name.out.log" 2>>"$LOG_ROOT/$name.err.log" &
    MARKETBOT_CHILD_PIDS+=("$!")
    MARKETBOT_CHILD_NAMES+=("$name")
    echo "Started $name (PID $!)"
  }

  check_children() {
    local index
    for index in "${!MARKETBOT_CHILD_PIDS[@]}"; do
      if ! kill -0 "${MARKETBOT_CHILD_PIDS[$index]}" 2>/dev/null; then
        echo "${MARKETBOT_CHILD_NAMES[$index]} exited unexpectedly." >&2
        echo "Inspect $LOG_ROOT/${MARKETBOT_CHILD_NAMES[$index]}.err.log" >&2
        return 1
      fi
    done
  }

  mkdir -p "$STATUS_ROOT" "$LOG_ROOT"
  validate_log_rotation_settings
  rotate_runtime_logs
  log_rotation_loop &
  LOG_ROTATOR_PID="$!"
  write_runtime_plan
  if [[ "${MARKETBOT_LINUX_READINESS_CLEARED:-0}" != "1" ]]; then
    clear_runtime_readiness
  fi

  echo "Starting independent MarketBot processes..."
  echo "Project: $PROJECT_ROOT"
  echo "Runtime: $RUNTIME_ROOT"
  echo "Definition: $DEFINITION_PATH"

  local batch_line name
  local -a batch_names=()
  local -a automatic_batch_names=()
  local -a process_arguments=()
  local -a batch_ready_paths=()
  while IFS= read -r batch_line; do
    IFS=$'\t' read -r -a batch_names <<<"$batch_line"
    automatic_batch_names=()
    for name in "${batch_names[@]}"; do
      if process_starts_manually "$name"; then
        echo "Leaving manual process stopped: $name"
        continue
      fi
      automatic_batch_names+=("$name")
      mapfile -d '' -t process_arguments < <(plan_process_arguments "$name")
      start_background "$name" "${process_arguments[@]}"
    done
    mapfile -d '' -t batch_ready_paths < <(
      plan_ready_paths "${automatic_batch_names[@]}"
    )
    ((${#batch_ready_paths[@]} == 0)) || wait_ready "${batch_ready_paths[@]}"
  done < <(plan_startup_batches)

  echo "All automatic processes ready. Logs: $LOG_ROOT"
  echo "Press Ctrl+C here to stop every process."

  while :; do
    check_children
    sleep 1
  done
}

launch_tmux() {
  command -v uv >/dev/null || { echo "uv is not installed or not in PATH." >&2; exit 1; }
  command -v tmux >/dev/null || {
    echo "tmux is required. Install it with: sudo apt install tmux" >&2
    exit 1
  }
  [[ -z "${TMUX:-}" ]] || {
    echo "Run this launcher outside an existing tmux session." >&2
    exit 1
  }
  prepare_runtime
  write_runtime_plan
  export MARKETBOT_LINUX_RUNTIME="$RUNTIME_ROOT"
  export MARKETBOT_LINUX_SYMBOLS="$SYMBOLS"
  export MARKETBOT_LINUX_NO_BELL="$NO_BELL"
  export MARKETBOT_LINUX_READY_TIMEOUT="$READY_TIMEOUT"
  export MARKETBOT_LINUX_SESSION="$SESSION"

  local base=("$SCRIPT_PATH" --runtime-root "$RUNTIME_ROOT" --definition-path "$DEFINITION_PATH" --ready-timeout "$READY_TIMEOUT" --session "$SESSION")
  [[ -n "$SYMBOLS" ]] && base+=(--symbols "$SYMBOLS")
  ((NO_BELL)) && base+=(--no-bell)
  local control analysis confirmed opportunities order_flow long_portfolio news geri_4h swing_trade patreon_analysis patreon_alerts elliott_wave support_confirmation signal_fusion_analysis signal_fusion_buys
  printf -v control '%q ' "${base[@]}" --role control
  printf -v analysis '%q ' "${base[@]}" --role analysis
  printf -v confirmed '%q ' "${base[@]}" --role confirmed
  printf -v opportunities '%q ' "${base[@]}" --role opportunities
  printf -v order_flow '%q ' "${base[@]}" --role order-flow-monitor
  printf -v long_portfolio '%q ' "${base[@]}" --role long-portfolio
  printf -v news '%q ' "${base[@]}" --role news
  printf -v geri_4h '%q ' "${base[@]}" --role 4hgeri
  printf -v swing_trade '%q ' "${base[@]}" --role swing-trade
  printf -v patreon_analysis '%q ' "${base[@]}" --role patreon-analysis
  printf -v patreon_alerts '%q ' "${base[@]}" --role patreon-alerts
  printf -v elliott_wave '%q ' "${base[@]}" --role elliott-wave
  printf -v support_confirmation '%q ' "${base[@]}" --role support-confirmation
  printf -v signal_fusion_analysis '%q ' "${base[@]}" --role signal-fusion-analysis
  printf -v signal_fusion_buys '%q ' "${base[@]}" --role signal-fusion-buys

  if tmux has-session -t "$SESSION" 2>/dev/null && ! runtime_matches_plan; then
    echo "MarketBot assembly changed; restarting the stale tmux runtime."
    "$PROJECT_ROOT/scripts/linux/stop-market-bot.sh" --session "$SESSION"
    clear_runtime_readiness
  fi

  if tmux has-session -t "$SESSION" 2>/dev/null; then
    if engine_is_active entry-opportunity && \
      ! tmux list-windows -t "$SESSION" -F '#W' | grep -Fxq 'Opportunities'; then
      tmux new-window -d -t "$SESSION" -n Opportunities "$opportunities"
      tmux set-window-option -t "$SESSION":Opportunities remain-on-exit on
      tmux select-pane -t "$SESSION":Opportunities.0 -T 'ENTRY OPPORTUNITIES'
    fi
    if engine_is_active order-flow && \
      ! tmux list-windows -t "$SESSION" -F '#W' | grep -Fxq 'OrderFlow'; then
      tmux new-window -d -t "$SESSION" -n OrderFlow "$order_flow"
      tmux set-window-option -t "$SESSION":OrderFlow remain-on-exit on
      tmux select-pane -t "$SESSION":OrderFlow.0 -T 'ORDER FLOW — ASTS/ASTX/ASTN/NBIS/NBIZ'
    fi
    if engine_is_active long-portfolio && \
      ! tmux list-windows -t "$SESSION" -F '#W' | grep -Fxq 'Portfolio2026'; then
      local portfolio_pane
      portfolio_pane="$(tmux list-panes -t "$SESSION":MarketBot -F '#{pane_id}|#{pane_title}' 2>/dev/null | awk -F'|' '$2 == "LONG PORTFOLIO 2026" { print $1; exit }')"
      if [[ -n "$portfolio_pane" ]]; then
        tmux break-pane -d -s "$portfolio_pane" -n Portfolio2026
      else
        tmux new-window -d -t "$SESSION" -n Portfolio2026 "$long_portfolio"
      fi
      tmux set-window-option -t "$SESSION":Portfolio2026 remain-on-exit on
      tmux select-pane -t "$SESSION":Portfolio2026.0 -T 'LONG PORTFOLIO 2026'
    fi
    if engine_is_active alert && \
      ! tmux list-windows -t "$SESSION" -F '#W' | grep -Fxq 'Analysis'; then
      local analysis_pane
      analysis_pane="$(tmux list-panes -t "$SESSION":MarketBot -F '#{pane_id}|#{pane_title}' 2>/dev/null | awk -F'|' '$2 == "ANÁLISIS" { print $1; exit }')"
      if [[ -n "$analysis_pane" ]]; then
        tmux break-pane -d -s "$analysis_pane" -n Analysis
      else
        tmux new-window -d -t "$SESSION" -n Analysis "$analysis"
      fi
      tmux set-window-option -t "$SESSION":Analysis remain-on-exit on
      tmux select-pane -t "$SESSION":Analysis.0 -T 'ANÁLISIS'
    fi
    if ! tmux list-windows -t "$SESSION" -F '#W' | grep -Fxq 'News'; then
      tmux new-window -d -t "$SESSION" -n News "$news"
      tmux set-window-option -t "$SESSION":News remain-on-exit on
      tmux select-pane -t "$SESSION":News.0 -T 'ALPACA NEWS — TENENCIAS DESTACADAS'
    fi
    if engine_is_active 4hgeri && \
      ! tmux list-windows -t "$SESSION" -F '#W' | grep -Fxq '4HGERI'; then
      tmux new-window -d -t "$SESSION" -n 4HGERI "$geri_4h"
      tmux set-window-option -t "$SESSION":4HGERI remain-on-exit on
      tmux select-pane -t "$SESSION":4HGERI.0 -T '4HGERI — NIVELES HORIZONTALES'
    fi
    if engine_is_active swing-trade && \
      ! tmux list-windows -t "$SESSION" -F '#W' | grep -Fxq 'SwingTrade'; then
      tmux new-window -d -t "$SESSION" -n SwingTrade "$swing_trade"
      tmux set-window-option -t "$SESSION":SwingTrade remain-on-exit on
      tmux set-window-option -t "$SESSION":SwingTrade history-limit 50000
      tmux select-pane -t "$SESSION":SwingTrade.0 -T 'SWING TRADE — FIBONACCI WATCHLIST'
    fi
    tmux select-layout -t "$SESSION":MarketBot even-vertical 2>/dev/null || true
    if engine_is_active patreon-caps && \
      ! tmux list-windows -t "$SESSION" -F '#W' | grep -Fxq 'PatreonCaps'; then
      tmux new-window -d -t "$SESSION" -n PatreonCaps "$patreon_analysis"
      tmux split-window -v -t "$SESSION":PatreonCaps "$patreon_alerts"
      tmux select-pane -t "$SESSION":PatreonCaps.0 -T 'PATREON CAPS — ANÁLISIS'
      tmux select-pane -t "$SESSION":PatreonCaps.1 -T 'PATREON CAPS — ALERTAS'
    fi
    if engine_is_active elliott-wave && \
      ! tmux list-windows -t "$SESSION" -F '#W' | grep -Fxq 'ElliottWave'; then
      tmux new-window -d -t "$SESSION" -n ElliottWave "$elliott_wave"
      tmux set-window-option -t "$SESSION":ElliottWave remain-on-exit on
      tmux select-pane -t "$SESSION":ElliottWave.0 -T 'ELLIOTT WAVE — TENENCIAS'
    fi
    if engine_is_active support-confirmation && \
      ! tmux list-windows -t "$SESSION" -F '#W' | grep -Fxq 'SupportConfirmation'; then
      tmux new-window -d -t "$SESSION" -n SupportConfirmation "$support_confirmation"
      tmux set-window-option -t "$SESSION":SupportConfirmation remain-on-exit on
      tmux select-pane -t "$SESSION":SupportConfirmation.0 -T 'SUPPORT CONFIRMATION — TENENCIAS'
    fi
    if engine_is_active signal-fusion && \
      ! tmux list-windows -t "$SESSION" -F '#W' | grep -Fxq 'SignalFusion'; then
      tmux new-window -d -t "$SESSION" -n SignalFusion "$signal_fusion_analysis"
      tmux set-window-option -t "$SESSION":SignalFusion remain-on-exit on
      tmux split-window -v -t "$SESSION":SignalFusion "$signal_fusion_buys"
      tmux select-pane -t "$SESSION":SignalFusion.0 -T 'FUSION — Z/R/S + GATES'
      tmux select-pane -t "$SESSION":SignalFusion.1 -T 'FUSION — BUY CONFIRMED'
    fi
    ((DETACH)) && return
    exec tmux attach-session -t "$SESSION"
  fi

  # A failed control pane can leave detached process groups behind. They keep
  # exclusive JetStream durables bound and make every later startup fail.
  "$PROJECT_ROOT/scripts/linux/stop-market-bot.sh" --session "$SESSION"
  clear_runtime_readiness
  export MARKETBOT_LINUX_READINESS_CLEARED=1
  tmux new-session -d -s "$SESSION" -n MarketBot "$control"
  tmux set-window-option -t "$SESSION":0 window-size latest
  tmux set-option -t "$SESSION" pane-border-status top
  tmux set-option -t "$SESSION" pane-border-format '#{pane_title}'
  tmux set-window-option -t "$SESSION":0 remain-on-exit on
  tmux select-pane -t "$SESSION":0.0 -T 'MARKETBOT CONTROL — Ctrl+C stops all'
  local pane_id
  if engine_is_active alert; then
    pane_id="$(tmux split-window -v -P -F '#{pane_id}' -t "$SESSION":0 "$confirmed")"
    tmux select-pane -t "$pane_id" -T 'COMPRAS CONFIRMADAS'
    tmux select-layout -t "$SESSION":0 even-vertical
  fi
  if engine_is_active entry-opportunity; then
    tmux new-window -d -t "$SESSION" -n Opportunities "$opportunities"
    tmux set-window-option -t "$SESSION":Opportunities remain-on-exit on
    tmux select-pane -t "$SESSION":Opportunities.0 -T 'ENTRY OPPORTUNITIES'
  fi
  if engine_is_active order-flow; then
    tmux new-window -d -t "$SESSION" -n OrderFlow "$order_flow"
    tmux set-window-option -t "$SESSION":OrderFlow remain-on-exit on
    tmux select-pane -t "$SESSION":OrderFlow.0 -T 'ORDER FLOW — ASTS/ASTX/ASTN/NBIS/NBIZ'
  fi
  if engine_is_active long-portfolio; then
    tmux new-window -d -t "$SESSION" -n Portfolio2026 "$long_portfolio"
    tmux set-window-option -t "$SESSION":Portfolio2026 remain-on-exit on
    tmux select-pane -t "$SESSION":Portfolio2026.0 -T 'LONG PORTFOLIO 2026'
  fi
  if engine_is_active alert; then
    tmux new-window -d -t "$SESSION" -n Analysis "$analysis"
    tmux set-window-option -t "$SESSION":Analysis remain-on-exit on
    tmux select-pane -t "$SESSION":Analysis.0 -T 'ANÁLISIS'
  fi
  tmux new-window -d -t "$SESSION" -n News "$news"
  tmux set-window-option -t "$SESSION":News remain-on-exit on
  tmux select-pane -t "$SESSION":News.0 -T 'ALPACA NEWS — TENENCIAS DESTACADAS'
  if engine_is_active 4hgeri; then
    tmux new-window -d -t "$SESSION" -n 4HGERI "$geri_4h"
    tmux set-window-option -t "$SESSION":4HGERI remain-on-exit on
    tmux select-pane -t "$SESSION":4HGERI.0 -T '4HGERI — NIVELES HORIZONTALES'
  fi
  if engine_is_active swing-trade; then
    tmux new-window -d -t "$SESSION" -n SwingTrade "$swing_trade"
    tmux set-window-option -t "$SESSION":SwingTrade remain-on-exit on
    tmux set-window-option -t "$SESSION":SwingTrade history-limit 50000
    tmux select-pane -t "$SESSION":SwingTrade.0 -T 'SWING TRADE — FIBONACCI WATCHLIST'
  fi
  if engine_is_active patreon-caps; then
    tmux new-window -d -t "$SESSION" -n PatreonCaps "$patreon_analysis"
    tmux set-window-option -t "$SESSION":PatreonCaps remain-on-exit on
    tmux split-window -v -t "$SESSION":PatreonCaps "$patreon_alerts"
    tmux select-pane -t "$SESSION":PatreonCaps.0 -T 'PATREON CAPS — ANÁLISIS'
    tmux select-pane -t "$SESSION":PatreonCaps.1 -T 'PATREON CAPS — ALERTAS'
  fi
  if engine_is_active elliott-wave; then
    tmux new-window -d -t "$SESSION" -n ElliottWave "$elliott_wave"
    tmux set-window-option -t "$SESSION":ElliottWave remain-on-exit on
    tmux select-pane -t "$SESSION":ElliottWave.0 -T 'ELLIOTT WAVE — TENENCIAS'
  fi
  if engine_is_active support-confirmation; then
    tmux new-window -d -t "$SESSION" -n SupportConfirmation "$support_confirmation"
    tmux set-window-option -t "$SESSION":SupportConfirmation remain-on-exit on
    tmux select-pane -t "$SESSION":SupportConfirmation.0 -T 'SUPPORT CONFIRMATION — TENENCIAS'
  fi
  if engine_is_active signal-fusion; then
    tmux new-window -d -t "$SESSION" -n SignalFusion "$signal_fusion_analysis"
    tmux set-window-option -t "$SESSION":SignalFusion remain-on-exit on
    tmux split-window -v -t "$SESSION":SignalFusion "$signal_fusion_buys"
    tmux select-pane -t "$SESSION":SignalFusion.0 -T 'FUSION — Z/R/S + GATES'
    tmux select-pane -t "$SESSION":SignalFusion.1 -T 'FUSION — BUY CONFIRMED'
  fi
  tmux select-pane -t "$SESSION":0.0
  ((DETACH)) && return
  exec tmux attach-session -t "$SESSION"
}

case "$ROLE" in
  launcher) launch_tmux ;;
  control) run_control ;;
  analysis) run_analysis ;;
  confirmed) run_confirmed ;;
  opportunities) run_opportunities ;;
  order-flow-monitor) run_order_flow_monitor ;;
  order-flow) run_manual_plan_process order-flow ;;
  long-portfolio) run_long_portfolio_monitor ;;
  news) run_news ;;
  4hgeri) run_4hgeri ;;
  swing-trade) run_swing_trade ;;
  patreon-analysis) run_patreon_caps_analysis ;;
  patreon-alerts) run_patreon_caps_alerts ;;
  elliott-wave) run_elliott_wave ;;
  support-confirmation) run_support_confirmation ;;
  signal-fusion-analysis) run_signal_fusion_analysis ;;
  signal-fusion-buys) run_signal_fusion_buys ;;
  *) echo "Invalid internal role: $ROLE" >&2; exit 2 ;;
esac
