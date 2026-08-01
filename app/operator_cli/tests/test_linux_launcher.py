from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[3]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "linux" / "start-market-bot.sh"


def test_linux_launcher_starts_long_portfolio_engine_and_tmux_pane() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "start_background long-portfolio-v1 run marketbot engine long-portfolio" in script
    assert 'run marketbot alerts long-portfolio' in script
    assert "--role long-portfolio" in script
    assert "LONG PORTFOLIO 2026" in script
    assert '"$STATUS_ROOT/long-portfolio-v1.ready.json"' in script
    assert '"$STATUS_ROOT/long-portfolio-monitor.ready.json"' in script
    assert "start_background entry-watcher-v3" in script
    assert "remain-on-exit on" in script
    assert 'tmux kill-session -t "$SESSION"' in script
    assert "tmux kill-pane -a" not in script
