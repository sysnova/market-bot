from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[3]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "linux" / "start-market-bot.sh"
STOP_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "linux" / "stop-market-bot.sh"


def test_linux_launcher_starts_long_portfolio_engine_and_tmux_pane() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "marketbot runtime-plan" in script
    assert "runtime-slots --mode active" not in script
    assert "engine_is_active" in script
    assert "plan_startup_batches" in script
    assert "plan_process_arguments" in script
    assert 'start_background "$name" "${process_arguments[@]}"' in script
    assert "run marketbot alerts long-portfolio" in script
    assert "--role long-portfolio" in script
    assert "-n Portfolio2026" in script
    assert "LONG PORTFOLIO 2026" in script
    assert '"$STATUS_ROOT/long-portfolio-monitor.ready.json"' in script
    assert "remain-on-exit on" in script
    assert 'tmux kill-session -t "$SESSION"' in script
    assert "tmux kill-pane -a" not in script


def test_linux_launcher_prepares_current_definition_environment() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    launcher = script.split("launch_tmux()", 1)[1].split('case "$ROLE"', 1)[0]

    assert "configs/marketbot/7.42.0.yaml" in script
    assert 'DEFINITION_PATH="$PROJECT_ROOT/configs/marketbot/7.42.0.yaml"' in script
    assert 'DEFINITION_PATH="${MARKETBOT_DEFINITION_PATH:-' not in script
    assert '--definition-path "$DEFINITION_PATH"' in launcher
    assert '--definition-path) DEFINITION_PATH="$2"; shift 2 ;;' in script
    assert 'export MARKETBOT_DEFINITION_PATH="$DEFINITION_PATH"' in script
    assert "uv sync --frozen" in script
    assert launcher.index("prepare_runtime") < launcher.index("write_runtime_plan")


def test_linux_launcher_restarts_session_when_effective_plan_changed() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    launcher = script.split("launch_tmux()", 1)[1].split('case "$ROLE"', 1)[0]

    assert "runtime_matches_plan" in script
    stale_guard = 'tmux has-session -t "$SESSION" 2>/dev/null && ! runtime_matches_plan'
    assert stale_guard in launcher
    assert launcher.index(stale_guard) < launcher.index(
        'if tmux has-session -t "$SESSION" 2>/dev/null; then'
    )


def test_linux_launcher_adds_event_driven_entry_opportunity_window() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "run marketbot monitor entry-opportunity" in script
    assert "--role opportunities" in script
    assert "entry-opportunity-monitor.ready.json" in script
    assert "-n Opportunities" in script
    assert "ENTRY OPPORTUNITIES" in script
    assert "list-windows" in script


def test_linux_launcher_starts_order_flow_from_the_runtime_plan() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    control = script.split("run_control()", 1)[1].split("launch_tmux()", 1)[0]

    assert "MANUAL_START_PROCESSES=()" in script
    assert 'process_starts_manually "$name"' in control
    assert 'automatic_batch_names+=("$name")' in control
    assert 'plan_ready_paths "${automatic_batch_names[@]}"' in control
    assert '"${MANUAL_START_PROCESSES[@]}"' in script
    assert "--role order-flow" in script
    assert "order-flow) run_manual_plan_process order-flow ;;" in script


def test_linux_launcher_adds_order_flow_dashboard_window() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "run marketbot monitor order-flow" in script
    assert "--role order-flow-monitor" in script
    assert "order-flow-monitor.ready.json" in script
    assert '-n OrderFlow "$order_flow"' in script
    assert "ORDER FLOW — ASTS/ASTX/ASTN/NBIS/NBIZ" in script
    assert "Esperando que el engine Order Flow publique readiness" in script
    assert "validate_order_flow_readiness" in script
    assert "Order Flow readiness does not match the selected definition" in script


def test_linux_launcher_adds_independent_alpaca_news_window() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "run marketbot monitor news" in script
    assert "--role news" in script
    assert "news-monitor.ready.json" in script
    assert '-n News "$news"' in script
    assert "ALPACA NEWS" in script


def test_linux_launcher_adds_independent_four_hour_geri_window() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "run marketbot monitor 4hgeri" in script
    assert "--role 4hgeri" in script
    assert 'wait_ready "$STATUS_ROOT/4hgeri.ready.json"' in script
    assert '"$STATUS_ROOT/4hgeri-monitor.ready.json"' in script
    assert '-n 4HGERI "$geri_4h"' in script
    assert "4HGERI — NIVELES HORIZONTALES" in script


def test_linux_launcher_adds_dedicated_swing_trade_dashboard() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "run marketbot monitor swing-trade" in script
    assert "--role swing-trade" in script
    assert 'wait_ready "$STATUS_ROOT/swing-trade.ready.json"' in script
    assert '"$STATUS_ROOT/swing-trade-monitor.ready.json"' in script
    assert '-n SwingTrade "$swing_trade"' in script
    assert "SWING TRADE — FIBONACCI WATCHLIST" in script
    assert 'SwingTrade history-limit 50000' in script


def test_linux_launcher_inherits_stock_analyzer_openai_key_without_sourcing_env() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "load_shared_openai_key" in script
    assert "stock-analyzer/apps/alert-runner/.env" in script
    assert 'export MARKETBOT_OPENAI_API_KEY="$value"' in script
    assert "source \"$STOCK_ANALYZER_ENV\"" not in script


def test_linux_launcher_keeps_only_control_and_confirmed_buys_in_main_window() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    fresh_session = script.split('tmux new-session -d -s "$SESSION"', 1)[1].split(
        "if engine_is_active patreon-caps", 1
    )[0]
    main_window = fresh_session.split("if engine_is_active entry-opportunity", 1)[0]

    assert main_window.count("tmux split-window") == 1
    assert '"$confirmed"' in main_window
    assert '"$analysis"' not in main_window
    assert '"$long_portfolio"' not in main_window
    assert 'select-layout -t "$SESSION":0 even-vertical' in main_window
    assert 'tmux new-window -d -t "$SESSION" -n Opportunities "$opportunities"' in (
        fresh_session
    )
    assert 'tmux new-window -d -t "$SESSION" -n Portfolio2026 "$long_portfolio"' in (
        fresh_session
    )
    assert 'tmux new-window -d -t "$SESSION" -n Analysis "$analysis"' in fresh_session
    assert fresh_session.index("-n Opportunities") < fresh_session.index("-n Portfolio2026")
    assert fresh_session.index("-n Portfolio2026") < fresh_session.index("-n Analysis")


def test_linux_launcher_migrates_legacy_monitor_panes_when_reattaching() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    reused_session = script.split('if tmux has-session -t "$SESSION"', 1)[1].split(
        "((DETACH)) && return", 1
    )[0]

    assert 'tmux break-pane -d -s "$portfolio_pane" -n Portfolio2026' in reused_session
    assert 'tmux break-pane -d -s "$analysis_pane" -n Analysis' in reused_session
    assert 'select-layout -t "$SESSION":MarketBot even-vertical' in reused_session


def test_linux_launcher_uses_canonical_batches_and_readiness_paths() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert 'done < <(plan_startup_batches)' in script
    assert 'plan_ready_paths "${automatic_batch_names[@]}"' in script
    assert 'wait_ready "${batch_ready_paths[@]}"' in script
    assert "start_background market-history-v1 run marketbot market history" not in script


def test_linux_launcher_runs_from_project_and_fails_fast_while_waiting() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    control = script.split("run_control()", 1)[1].split("launch_tmux()", 1)[0]
    wait_ready = script.split("wait_ready()", 1)[1].split("run_control()", 1)[0]

    assert 'cd "$PROJECT_ROOT"' in control
    assert "declare -F check_children" in wait_ready
    assert "check_children || return 1" in wait_ready


def test_linux_launcher_allows_large_history_bootstrap() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "READY_TIMEOUT=1800" in script
    assert "Readiness timeout (default: 1800)." in script


def test_linux_launcher_bounds_process_logs() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    control = script.split("run_control()", 1)[1].split("launch_tmux()", 1)[0]

    assert 'LOG_MAX_BYTES="${MARKETBOT_LOG_MAX_BYTES:-52428800}"' in script
    assert 'LOG_BACKUP_COUNT="${MARKETBOT_LOG_BACKUP_COUNT:-3}"' in script
    assert (
        'LOG_ROTATION_INTERVAL_SECONDS="${MARKETBOT_LOG_ROTATION_INTERVAL_SECONDS:-60}"'
        in script
    )
    assert "rotate_runtime_logs" in control
    assert "log_rotation_loop" in control
    assert '>>"$LOG_ROOT/$name.out.log" 2>>"$LOG_ROOT/$name.err.log"' in control


def test_linux_launcher_cleanup_signals_groups_and_direct_children() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    cleanup = script.split("cleanup()", 1)[1].split("start_background()", 1)[0]

    assert 'kill -TERM -- "-$pid"' in cleanup
    assert 'kill -TERM "$pid"' in cleanup
    assert 'kill -KILL -- "-$pid"' in cleanup
    assert 'kill -KILL "$pid"' in cleanup
    assert '|| kill -TERM "$pid"' not in cleanup
    assert '|| kill -KILL "$pid"' not in cleanup


def test_linux_launcher_keeps_child_registry_alive_for_exit_cleanup() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    control = script.split("run_control()", 1)[1].split("launch_tmux()", 1)[0]

    assert "MARKETBOT_CHILD_PIDS=()" in control
    assert "MARKETBOT_CHILD_NAMES=()" in control
    assert 'for pid in "${MARKETBOT_CHILD_PIDS[@]}"' in control
    assert 'MARKETBOT_CHILD_PIDS+=("$!")' in control
    assert "local -a child_pids=()" not in control


def test_linux_launcher_stops_orphans_before_creating_a_fresh_session() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    launch = script.split("launch_tmux()", 1)[1].split('case "$ROLE"', 1)[0]

    stop_orphans = '"$PROJECT_ROOT/scripts/linux/stop-market-bot.sh" --session "$SESSION"'
    assert stop_orphans in launch
    assert launch.index(stop_orphans) < launch.index("tmux new-session")


def test_linux_launcher_clears_readiness_before_starting_monitor_panes() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    launch = script.split("launch_tmux()", 1)[1].split('case "$ROLE"', 1)[0]

    assert "clear_runtime_readiness" in script
    assert launch.index("clear_runtime_readiness") < launch.index("tmux new-session")
    assert "MARKETBOT_LINUX_READINESS_CLEARED=1" in launch


def test_linux_launcher_preserves_arguments_without_shell_reparsing() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "mapfile -d '' -t process_arguments" in script
    assert "sys.stdout.buffer.write" in script
    assert "eval " not in script


def test_linux_launcher_executes_marketbot_without_resident_uv_wrappers() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    start_background = script.split("start_background()", 1)[1].split(
        "check_children()", 1
    )[0]

    assert 'MARKETBOT_EXECUTABLE="$UV_PROJECT_ENVIRONMENT/bin/marketbot"' in script
    assert 'setsid "$MARKETBOT_EXECUTABLE" "${@:3}"' in start_background
    assert "setsid uv \"$@\"" not in start_background
    assert "exec_marketbot" in script


def test_analytical_tmux_roles_are_read_only_monitors() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    for start, end in (
        ("run_4hgeri()", "run_swing_trade()"),
        ("run_swing_trade()", "run_patreon_caps_analysis()"),
        ("run_patreon_caps_analysis()", "run_patreon_caps_alerts()"),
        ("run_elliott_wave()", "run_support_confirmation()"),
        ("run_support_confirmation()", "run_signal_fusion_analysis()"),
    ):
        role = script.split(start, 1)[1].split(end, 1)[0]
        assert "marketbot monitor" in role
        assert "marketbot engine" not in role
        assert "wait -n" not in role
        assert "kill " not in role

    new_session = script.split('tmux new-session -d -s "$SESSION"', 1)[0]
    assert "write_runtime_plan" in new_session


def test_market_stream_is_gated_only_by_headless_engine_readiness() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    control = script.split("run_control()", 1)[1].split("launch_tmux()", 1)[0]

    for monitor in (
        "confirmed-buy-monitor",
        "entry-opportunity-monitor",
        "4hgeri-monitor",
        "swing-trade-monitor",
        "patreon-caps-analysis",
        "elliott-wave-analysis",
        "support-confirmation-analysis",
        "signal-fusion-analysis",
        "signal-fusion-buys",
    ):
        assert f'wait_ready "$STATUS_ROOT/{monitor}.ready.json"' not in control


def test_linux_launcher_starts_patreon_caps_and_dedicated_tmux_window() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "plan_process_arguments" in script
    assert "run marketbot monitor patreon-caps" in script
    assert "run marketbot alerts patreon-caps" in script
    assert "-n PatreonCaps" in script
    assert "PATREON CAPS — ANÁLISIS" in script
    assert "PATREON CAPS — ALERTAS" in script
    assert "list-windows" in script


def test_linux_stop_script_targets_only_marketbot_commands_and_dedicated_sessions() -> None:
    script = STOP_SCRIPT_PATH.read_text(encoding="utf-8")

    assert '[[ "$argument" == "marketbot" ]]' in script
    assert '[[ "$basename" == "marketbot" ]]' in script
    assert '[[ "$basename" == "start-market-bot.sh" ]]' in script
    assert "kill -TERM" in script
    assert "kill -KILL" in script
    assert "TMUX_SESSIONS=(marketbot marketbot-long)" in script
    assert "--dry-run" in script
    assert "pkill" not in script
