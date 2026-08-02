#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT_PATH="$PROJECT_ROOT/scripts/linux/start-market-bot.sh"
ROLE="launcher"
RUNTIME_ROOT="$PROJECT_ROOT/.runtime"
SYMBOLS=""
NO_BELL=0
READY_TIMEOUT=600
SESSION="marketbot"

usage() {
  cat <<'EOF'
Usage: ./scripts/linux/start-market-bot.sh [options]

Options:
  --symbols AAPL,MSFT   Override the PostgreSQL universe for this run.
  --runtime-root PATH   Runtime directory (default: .runtime).
  --no-bell             Disable alert bells.
  --ready-timeout SEC   Readiness timeout (default: 600).
  --session NAME        tmux session name (default: marketbot).
  -h, --help            Show this help.
EOF
}

while (($#)); do
  case "$1" in
    --role) ROLE="$2"; shift 2 ;;
    --symbols) SYMBOLS="$2"; shift 2 ;;
    --runtime-root) RUNTIME_ROOT="$2"; shift 2 ;;
    --no-bell) NO_BELL=1; shift ;;
    --ready-timeout) READY_TIMEOUT="$2"; shift 2 ;;
    --session) SESSION="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$RUNTIME_ROOT" != /* ]]; then
  RUNTIME_ROOT="$PROJECT_ROOT/$RUNTIME_ROOT"
fi

STATUS_ROOT="$RUNTIME_ROOT/status"
LOG_ROOT="$RUNTIME_ROOT/logs"

run_analysis() {
  local args=(run marketbot alerts serve --runtime-root "$RUNTIME_ROOT" \
    --ready-path "$STATUS_ROOT/alert-v2.ready.json")
  ((NO_BELL)) && args+=(--no-bell)
  cd "$PROJECT_ROOT"
  exec uv "${args[@]}"
}

run_confirmed() {
  local args=(run marketbot alerts confirmed \
    --ready-path "$STATUS_ROOT/confirmed-buy-monitor.ready.json")
  ((NO_BELL)) && args+=(--no-bell)
  cd "$PROJECT_ROOT"
  exec uv "${args[@]}"
}

run_long_portfolio_monitor() {
  local args=(run marketbot alerts long-portfolio \
    --ready-path "$STATUS_ROOT/long-portfolio-monitor.ready.json")
  ((NO_BELL)) && args+=(--no-bell)
  cd "$PROJECT_ROOT"
  exec uv "${args[@]}"
}

run_patreon_caps_analysis() {
  cd "$PROJECT_ROOT"
  mkdir -p "$STATUS_ROOT" "$LOG_ROOT"
  rm -f "$STATUS_ROOT/patreon-caps-v1.ready.json" \
    "$STATUS_ROOT/patreon-caps-analysis.ready.json"
  uv run marketbot engine patreon-caps \
    --ready-path "$STATUS_ROOT/patreon-caps-v1.ready.json" \
    >>"$LOG_ROOT/patreon-caps-v1.log" 2>&1 &
  local engine_pid=$!
  uv run marketbot monitor patreon-caps \
    --ready-path "$STATUS_ROOT/patreon-caps-analysis.ready.json" &
  local monitor_pid=$!
  cleanup_patreon() {
    trap - EXIT INT TERM
    kill "${engine_pid:-}" "${monitor_pid:-}" 2>/dev/null || true
    wait "${engine_pid:-}" "${monitor_pid:-}" 2>/dev/null || true
  }
  trap cleanup_patreon EXIT INT TERM
  local status=0
  wait -n "$engine_pid" "$monitor_pid" || status=$?
  cleanup_patreon
  return "$status"
}

run_patreon_caps_alerts() {
  local args=(run marketbot alerts patreon-caps \
    --ready-path "$STATUS_ROOT/patreon-caps-alerts.ready.json")
  ((NO_BELL)) && args+=(--no-bell)
  cd "$PROJECT_ROOT"
  exec uv "${args[@]}"
}

run_elliott_wave() {
  cd "$PROJECT_ROOT"
  mkdir -p "$STATUS_ROOT" "$LOG_ROOT"
  rm -f "$STATUS_ROOT/elliott-wave-v0.ready.json" \
    "$STATUS_ROOT/elliott-wave-analysis.ready.json"
  uv run marketbot engine elliott-wave \
    --ready-path "$STATUS_ROOT/elliott-wave-v0.ready.json" \
    >>"$LOG_ROOT/elliott-wave-v0.log" 2>&1 &
  local engine_pid=$!
  local monitor_pid=""
  cleanup_elliott() {
    trap - EXIT INT TERM
    kill "${engine_pid:-}" "${monitor_pid:-}" 2>/dev/null || true
    wait "${engine_pid:-}" "${monitor_pid:-}" 2>/dev/null || true
  }
  trap cleanup_elliott EXIT INT TERM
  while [[ ! -f "$STATUS_ROOT/elliott-wave-v0.ready.json" ]]; do
    if ! kill -0 "$engine_pid" 2>/dev/null; then
      local status=0
      wait "$engine_pid" || status=$?
      cleanup_elliott
      return "$status"
    fi
    sleep 0.2
  done
  uv run marketbot monitor elliott-wave \
    --ready-path "$STATUS_ROOT/elliott-wave-analysis.ready.json" &
  monitor_pid=$!
  local status=0
  wait -n "$engine_pid" "$monitor_pid" || status=$?
  cleanup_elliott
  return "$status"
}

run_support_confirmation() {
  cd "$PROJECT_ROOT"
  mkdir -p "$STATUS_ROOT" "$LOG_ROOT"
  rm -f "$STATUS_ROOT/support-confirmation-v0.ready.json" \
    "$STATUS_ROOT/support-confirmation-analysis.ready.json"
  uv run marketbot engine support-confirmation \
    --ready-path "$STATUS_ROOT/support-confirmation-v0.ready.json" \
    >>"$LOG_ROOT/support-confirmation-v0.log" 2>&1 &
  local engine_pid=$!
  local monitor_pid=""
  cleanup_support_confirmation() {
    trap - EXIT INT TERM
    kill "${engine_pid:-}" "${monitor_pid:-}" 2>/dev/null || true
    wait "${engine_pid:-}" "${monitor_pid:-}" 2>/dev/null || true
  }
  trap cleanup_support_confirmation EXIT INT TERM
  while [[ ! -f "$STATUS_ROOT/support-confirmation-v0.ready.json" ]]; do
    if ! kill -0 "$engine_pid" 2>/dev/null; then
      local status=0
      wait "$engine_pid" || status=$?
      cleanup_support_confirmation
      return "$status"
    fi
    sleep 0.2
  done
  uv run marketbot monitor support-confirmation \
    --ready-path "$STATUS_ROOT/support-confirmation-analysis.ready.json" &
  monitor_pid=$!
  local status=0
  wait -n "$engine_pid" "$monitor_pid" || status=$?
  cleanup_support_confirmation
  return "$status"
}

wait_ready() {
  local deadline=$((SECONDS + READY_TIMEOUT)) missing path
  while :; do
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
  local -a child_pids=()
  local -a child_names=()

  cleanup() {
    trap - EXIT INT TERM
    echo
    echo "Stopping every MarketBot process..."
    for pid in "${child_pids[@]}"; do
      kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    done
    sleep 1
    for pid in "${child_pids[@]}"; do
      kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
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
    setsid uv "$@" >"$LOG_ROOT/$name.out.log" 2>"$LOG_ROOT/$name.err.log" &
    child_pids+=("$!")
    child_names+=("$name")
    echo "Started $name (PID $!)"
  }

  check_children() {
    local index
    for index in "${!child_pids[@]}"; do
      if ! kill -0 "${child_pids[$index]}" 2>/dev/null; then
        echo "${child_names[$index]} exited unexpectedly." >&2
        echo "Inspect $LOG_ROOT/${child_names[$index]}.err.log" >&2
        return 1
      fi
    done
  }

  mkdir -p "$STATUS_ROOT" "$LOG_ROOT"
  rm -f "$STATUS_ROOT"/{alert-v2,entry-watcher-v2,entry-watcher-v3,long-term-v2,swing-v2,intraday-v2,market-rotation-v1,portfolio-flow-v1,long-portfolio-v1,confirmed-buy-monitor,long-portfolio-monitor,patreon-caps-v1,patreon-caps-analysis,patreon-caps-alerts,elliott-wave-v0,elliott-wave-analysis,support-confirmation-v0,support-confirmation-analysis}.ready.json

  echo "Starting independent MarketBot processes..."
  echo "Project: $PROJECT_ROOT"
  echo "Runtime: $RUNTIME_ROOT"

  start_background entry-watcher-v3 run marketbot entry-watch serve \
    --ready-path "$STATUS_ROOT/entry-watcher-v3.ready.json"
  wait_ready "$STATUS_ROOT/alert-v2.ready.json" "$STATUS_ROOT/entry-watcher-v3.ready.json"
  wait_ready "$STATUS_ROOT/confirmed-buy-monitor.ready.json"

  local symbol_args=()
  [[ -n "$SYMBOLS" ]] && symbol_args=(--symbols "$SYMBOLS")
  start_background long-term-v2 run marketbot engine long \
    --ready-path "$STATUS_ROOT/long-term-v2.ready.json" "${symbol_args[@]}"
  start_background swing-v2 run marketbot engine swing \
    --ready-path "$STATUS_ROOT/swing-v2.ready.json" "${symbol_args[@]}"
  start_background intraday-v2 run marketbot engine intraday \
    --ready-path "$STATUS_ROOT/intraday-v2.ready.json" "${symbol_args[@]}"
  start_background market-rotation-v1 run marketbot engine rotation \
    --ready-path "$STATUS_ROOT/market-rotation-v1.ready.json"
  start_background portfolio-flow-v1 run marketbot engine portfolio-flow \
    --ready-path "$STATUS_ROOT/portfolio-flow-v1.ready.json"
  start_background long-portfolio-v1 run marketbot engine long-portfolio \
    --runtime-root "$RUNTIME_ROOT" \
    --ready-path "$STATUS_ROOT/long-portfolio-v1.ready.json"
  wait_ready \
    "$STATUS_ROOT/long-term-v2.ready.json" \
    "$STATUS_ROOT/swing-v2.ready.json" \
    "$STATUS_ROOT/intraday-v2.ready.json" \
    "$STATUS_ROOT/market-rotation-v1.ready.json"
  wait_ready "$STATUS_ROOT/portfolio-flow-v1.ready.json"
  wait_ready "$STATUS_ROOT/long-portfolio-v1.ready.json" \
    "$STATUS_ROOT/long-portfolio-monitor.ready.json"
  wait_ready "$STATUS_ROOT/patreon-caps-v1.ready.json" \
    "$STATUS_ROOT/patreon-caps-analysis.ready.json" \
    "$STATUS_ROOT/patreon-caps-alerts.ready.json"
  wait_ready "$STATUS_ROOT/elliott-wave-v0.ready.json" \
    "$STATUS_ROOT/elliott-wave-analysis.ready.json"
  wait_ready "$STATUS_ROOT/support-confirmation-v0.ready.json" \
    "$STATUS_ROOT/support-confirmation-analysis.ready.json"

  start_background alpaca-market-stream run marketbot market stream "${symbol_args[@]}"
  echo "All engines ready. Logs: $LOG_ROOT"
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
  export MARKETBOT_LINUX_RUNTIME="$RUNTIME_ROOT"
  export MARKETBOT_LINUX_SYMBOLS="$SYMBOLS"
  export MARKETBOT_LINUX_NO_BELL="$NO_BELL"
  export MARKETBOT_LINUX_READY_TIMEOUT="$READY_TIMEOUT"
  export MARKETBOT_LINUX_SESSION="$SESSION"

  local base=("$SCRIPT_PATH" --runtime-root "$RUNTIME_ROOT" --ready-timeout "$READY_TIMEOUT" --session "$SESSION")
  [[ -n "$SYMBOLS" ]] && base+=(--symbols "$SYMBOLS")
  ((NO_BELL)) && base+=(--no-bell)
  local control analysis confirmed long_portfolio patreon_analysis patreon_alerts elliott_wave support_confirmation
  printf -v control '%q ' "${base[@]}" --role control
  printf -v analysis '%q ' "${base[@]}" --role analysis
  printf -v confirmed '%q ' "${base[@]}" --role confirmed
  printf -v long_portfolio '%q ' "${base[@]}" --role long-portfolio
  printf -v patreon_analysis '%q ' "${base[@]}" --role patreon-analysis
  printf -v patreon_alerts '%q ' "${base[@]}" --role patreon-alerts
  printf -v elliott_wave '%q ' "${base[@]}" --role elliott-wave
  printf -v support_confirmation '%q ' "${base[@]}" --role support-confirmation

  if tmux has-session -t "$SESSION" 2>/dev/null; then
    if ! tmux list-windows -t "$SESSION" -F '#W' | grep -Fxq 'PatreonCaps'; then
      tmux new-window -d -t "$SESSION" -n PatreonCaps "$patreon_analysis"
      tmux split-window -v -t "$SESSION":PatreonCaps "$patreon_alerts"
      tmux select-pane -t "$SESSION":PatreonCaps.0 -T 'PATREON CAPS — ANÁLISIS'
      tmux select-pane -t "$SESSION":PatreonCaps.1 -T 'PATREON CAPS — ALERTAS'
    fi
    if ! tmux list-windows -t "$SESSION" -F '#W' | grep -Fxq 'ElliottWave'; then
      tmux new-window -d -t "$SESSION" -n ElliottWave "$elliott_wave"
      tmux set-window-option -t "$SESSION":ElliottWave remain-on-exit on
      tmux select-pane -t "$SESSION":ElliottWave.0 -T 'ELLIOTT WAVE — TENENCIAS'
    fi
    if ! tmux list-windows -t "$SESSION" -F '#W' | grep -Fxq 'SupportConfirmation'; then
      tmux new-window -d -t "$SESSION" -n SupportConfirmation "$support_confirmation"
      tmux set-window-option -t "$SESSION":SupportConfirmation remain-on-exit on
      tmux select-pane -t "$SESSION":SupportConfirmation.0 -T 'SUPPORT CONFIRMATION — TENENCIAS'
    fi
    exec tmux attach-session -t "$SESSION"
  fi

  tmux new-session -d -s "$SESSION" -n MarketBot "$control"
  tmux set-window-option -t "$SESSION":0 window-size latest
  tmux set-option -t "$SESSION" pane-border-status top
  tmux set-option -t "$SESSION" pane-border-format '#{pane_title}'
  tmux set-window-option -t "$SESSION":0 remain-on-exit on
  tmux select-pane -t "$SESSION":0.0 -T 'MARKETBOT CONTROL — Ctrl+C stops all'
  tmux split-window -v -t "$SESSION":0 "$analysis"
  tmux select-pane -t "$SESSION":0.1 -T 'ANÁLISIS'
  tmux split-window -v -t "$SESSION":0 "$confirmed"
  tmux select-pane -t "$SESSION":0.2 -T 'COMPRAS CONFIRMADAS'
  tmux split-window -v -t "$SESSION":0 "$long_portfolio"
  tmux select-pane -t "$SESSION":0.3 -T 'LONG PORTFOLIO 2026'
  tmux select-layout -t "$SESSION":0 tiled
  tmux new-window -d -t "$SESSION" -n PatreonCaps "$patreon_analysis"
  tmux set-window-option -t "$SESSION":PatreonCaps remain-on-exit on
  tmux split-window -v -t "$SESSION":PatreonCaps "$patreon_alerts"
  tmux select-pane -t "$SESSION":PatreonCaps.0 -T 'PATREON CAPS — ANÁLISIS'
  tmux select-pane -t "$SESSION":PatreonCaps.1 -T 'PATREON CAPS — ALERTAS'
  tmux new-window -d -t "$SESSION" -n ElliottWave "$elliott_wave"
  tmux set-window-option -t "$SESSION":ElliottWave remain-on-exit on
  tmux select-pane -t "$SESSION":ElliottWave.0 -T 'ELLIOTT WAVE — TENENCIAS'
  tmux new-window -d -t "$SESSION" -n SupportConfirmation "$support_confirmation"
  tmux set-window-option -t "$SESSION":SupportConfirmation remain-on-exit on
  tmux select-pane -t "$SESSION":SupportConfirmation.0 -T 'SUPPORT CONFIRMATION — TENENCIAS'
  tmux select-pane -t "$SESSION":0.0
  exec tmux attach-session -t "$SESSION"
}

case "$ROLE" in
  launcher) launch_tmux ;;
  control) run_control ;;
  analysis) run_analysis ;;
  confirmed) run_confirmed ;;
  long-portfolio) run_long_portfolio_monitor ;;
  patreon-analysis) run_patreon_caps_analysis ;;
  patreon-alerts) run_patreon_caps_alerts ;;
  elliott-wave) run_elliott_wave ;;
  support-confirmation) run_support_confirmation ;;
  *) echo "Invalid internal role: $ROLE" >&2; exit 2 ;;
esac
