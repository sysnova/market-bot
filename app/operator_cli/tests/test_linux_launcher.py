from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[3]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "linux" / "start-market-bot.sh"
STOP_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "linux" / "stop-market-bot.sh"


def test_linux_launcher_starts_long_portfolio_engine_and_tmux_pane() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "start_background long-portfolio-v1 run marketbot engine long-portfolio" in script
    assert "run marketbot alerts long-portfolio" in script
    assert "--role long-portfolio" in script
    assert "LONG PORTFOLIO 2026" in script
    assert '"$STATUS_ROOT/long-portfolio-v1.ready.json"' in script
    assert '"$STATUS_ROOT/long-portfolio-monitor.ready.json"' in script
    assert "start_background entry-watcher" in script
    assert "start_background entry-opportunity" in script
    assert "start_background entry-recovery run marketbot engine entry-recovery" in script
    assert "start_background alert run marketbot alerts serve" in script
    assert "start_background outbox-relay run marketbot outbox serve" in script
    assert "run marketbot entry-opportunity serve" in script
    assert "remain-on-exit on" in script
    assert 'tmux kill-session -t "$SESSION"' in script
    assert "tmux kill-pane -a" not in script


def test_linux_launcher_adds_event_driven_entry_opportunity_window() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "run marketbot monitor entry-opportunity" in script
    assert "--role opportunities" in script
    assert "entry-opportunity-monitor.ready.json" in script
    assert "-n Opportunities" in script
    assert "ENTRY OPPORTUNITIES" in script
    assert "list-windows" in script


def test_linux_launcher_starts_market_history_before_analytical_engines() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    history = "start_background market-history-v1 run marketbot market history"
    long_term = "start_background long-term run marketbot engine long"
    assert history in script
    assert 'wait_ready "$STATUS_ROOT/market-history-v1.ready.json"' in script
    assert script.index(history) < script.index(long_term)


def test_linux_launcher_readies_long_portfolio_before_long_snapshot() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    portfolio = "start_background long-portfolio-v1 run marketbot engine long-portfolio"
    portfolio_ready = 'wait_ready "$STATUS_ROOT/long-portfolio-v1.ready.json"'
    long_term = "start_background long-term run marketbot engine long"
    assert script.index(portfolio) < script.index(portfolio_ready) < script.index(long_term)


def test_analytical_tmux_roles_are_read_only_monitors() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    for start, end in (
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
    assert 'rm -f "$STATUS_ROOT/market-history-v1.ready.json"' in new_session


def test_market_stream_is_gated_only_by_headless_engine_readiness() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    control = script.split("run_control()", 1)[1].split("launch_tmux()", 1)[0]
    before_stream = control.split("start_background alpaca-market-stream", 1)[0]

    for monitor in (
        "confirmed-buy-monitor",
        "entry-opportunity-monitor",
        "patreon-caps-analysis",
        "elliott-wave-analysis",
        "support-confirmation-analysis",
        "signal-fusion-analysis",
        "signal-fusion-buys",
    ):
        assert f'wait_ready "$STATUS_ROOT/{monitor}.ready.json"' not in before_stream


def test_linux_launcher_starts_patreon_caps_and_dedicated_tmux_window() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "start_background patreon-caps-v1 run marketbot engine patreon-caps" in script
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
