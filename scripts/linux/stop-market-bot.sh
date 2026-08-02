#!/usr/bin/env bash
set -Eeuo pipefail

DRY_RUN=0
TERM_TIMEOUT=8
CURRENT_UID="$(id -u)"
TMUX_SESSIONS=(marketbot marketbot-long)

usage() {
  cat <<'EOF'
Usage: ./scripts/linux/stop-market-bot.sh [options]

Stops every MarketBot CLI and Linux launcher process owned by the current user.
It does not stop NATS, PostgreSQL, Docker, or unrelated Python/uv processes.

Options:
  --dry-run          Show matching processes and tmux sessions without stopping them.
  --timeout SEC      Seconds to wait after SIGTERM before SIGKILL (default: 8).
  --session NAME     Also close this dedicated tmux session; may be repeated.
  -h, --help         Show this help.
EOF
}

while (($#)); do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --timeout)
      [[ $# -ge 2 ]] || { echo "--timeout requires a value." >&2; exit 2; }
      TERM_TIMEOUT="$2"
      shift 2
      ;;
    --session)
      [[ $# -ge 2 ]] || { echo "--session requires a name." >&2; exit 2; }
      TMUX_SESSIONS+=("$2")
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[[ "$TERM_TIMEOUT" =~ ^[0-9]+$ ]] && ((TERM_TIMEOUT <= 60)) || {
  echo "--timeout must be an integer between 0 and 60." >&2
  exit 2
}

process_owner_uid() {
  local pid="$1" key real_uid ignored
  while read -r key real_uid ignored; do
    if [[ "$key" == "Uid:" ]]; then
      printf '%s\n' "$real_uid"
      return 0
    fi
  done <"/proc/$pid/status" 2>/dev/null
  return 1
}

read_process_argv() {
  local pid="$1" argument
  PROCESS_ARGV=()
  while IFS= read -r -d '' argument; do
    PROCESS_ARGV+=("$argument")
  done <"/proc/$pid/cmdline" 2>/dev/null || true
  ((${#PROCESS_ARGV[@]} > 0))
}

argv_is_marketbot() {
  local executable="${PROCESS_ARGV[0]##*/}" argument basename
  case "$executable" in
    uv)
      for argument in "${PROCESS_ARGV[@]:1}"; do
        [[ "$argument" == "marketbot" ]] && return 0
      done
      ;;
    marketbot)
      return 0
      ;;
    python|python[0-9]*|python.exe)
      for argument in "${PROCESS_ARGV[@]:1}"; do
        basename="${argument##*/}"
        [[ "$basename" == "marketbot" ]] && return 0
      done
      ;;
    bash|sh)
      for argument in "${PROCESS_ARGV[@]:1}"; do
        basename="${argument##*/}"
        [[ "$basename" == "start-market-bot.sh" ]] && return 0
      done
      ;;
  esac
  return 1
}

pid_is_marketbot() {
  local pid="$1" owner
  [[ "$pid" =~ ^[0-9]+$ && "$pid" != "$$" && "$pid" != "$PPID" ]] || return 1
  [[ -r "/proc/$pid/status" && -r "/proc/$pid/cmdline" ]] || return 1
  owner="$(process_owner_uid "$pid")" || return 1
  [[ "$owner" == "$CURRENT_UID" ]] || return 1
  read_process_argv "$pid" || return 1
  argv_is_marketbot
}

collect_marketbot_processes() {
  local proc pid command_line
  MATCHED_PIDS=()
  MATCHED_COMMANDS=()
  for proc in /proc/[0-9]*; do
    pid="${proc##*/}"
    if pid_is_marketbot "$pid"; then
      printf -v command_line '%q ' "${PROCESS_ARGV[@]}"
      MATCHED_PIDS+=("$pid")
      MATCHED_COMMANDS+=("${command_line% }")
    fi
  done
}

collect_tmux_sessions() {
  local requested session
  local -a existing=()
  MATCHED_TMUX_SESSIONS=()
  command -v tmux >/dev/null 2>&1 || return 0
  mapfile -t existing < <(tmux list-sessions -F '#S' 2>/dev/null || true)
  for requested in "${TMUX_SESSIONS[@]}"; do
    for session in "${existing[@]}"; do
      if [[ "$session" == "$requested" ]]; then
        MATCHED_TMUX_SESSIONS+=("$session")
        break
      fi
    done
  done
}

show_matches() {
  local index
  if ((${#MATCHED_PIDS[@]} == 0)); then
    echo "No MarketBot processes found."
  else
    echo "MarketBot processes:"
    for index in "${!MATCHED_PIDS[@]}"; do
      printf '  PID %-7s %s\n' "${MATCHED_PIDS[$index]}" "${MATCHED_COMMANDS[$index]}"
    done
  fi
  if ((${#MATCHED_TMUX_SESSIONS[@]} > 0)); then
    echo "MarketBot tmux sessions:"
    printf '  %s\n' "${MATCHED_TMUX_SESSIONS[@]}"
  fi
}

collect_marketbot_processes
collect_tmux_sessions
show_matches

if ((DRY_RUN)); then
  echo "Dry run: nothing was stopped."
  exit 0
fi

if ((${#MATCHED_PIDS[@]} == 0 && ${#MATCHED_TMUX_SESSIONS[@]} == 0)); then
  exit 0
fi

if ((${#MATCHED_PIDS[@]} > 0)); then
  echo "Sending SIGTERM to ${#MATCHED_PIDS[@]} MarketBot processes..."
  kill -TERM "${MATCHED_PIDS[@]}" 2>/dev/null || true
fi

if ((${#MATCHED_TMUX_SESSIONS[@]} > 0)); then
  for session in "${MATCHED_TMUX_SESSIONS[@]}"; do
    tmux kill-session -t "$session" 2>/dev/null || true
  done
fi

deadline=$((SECONDS + TERM_TIMEOUT))
while ((SECONDS < deadline)); do
  collect_marketbot_processes
  ((${#MATCHED_PIDS[@]} == 0)) && break
  sleep 0.2
done

collect_marketbot_processes
if ((${#MATCHED_PIDS[@]} > 0)); then
  echo "Sending SIGKILL to ${#MATCHED_PIDS[@]} surviving MarketBot processes..."
  kill -KILL "${MATCHED_PIDS[@]}" 2>/dev/null || true
  sleep 0.2
fi

collect_marketbot_processes
if ((${#MATCHED_PIDS[@]} > 0)); then
  echo "Could not stop every MarketBot process:" >&2
  show_matches >&2
  exit 1
fi

echo "Every MarketBot process was stopped."
