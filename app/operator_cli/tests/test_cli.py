import asyncio
import json
import re
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from app.operator_cli.main import _run_async, app

runner = CliRunner()
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _plain(output: str) -> str:
    return _ANSI_ESCAPE.sub("", output)


def test_async_cli_commands_use_selector_event_loop() -> None:
    async def current_loop() -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    loop = _run_async(current_loop())

    assert isinstance(loop, asyncio.SelectorEventLoop)


def test_root_help_lists_operator_groups() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for group in (
        "rules",
        "strategy",
        "audit",
        "supervisor",
        "infra",
        "live",
        "sec",
        "engine",
        "market",
        "alerts",
        "entry-watch",
        "monitor",
    ):
        assert group in result.stdout


def test_distributed_process_commands_are_explicit() -> None:
    for command in (
        ("engine", "long"),
        ("engine", "swing"),
        ("engine", "intraday"),
        ("market", "stream"),
        ("market", "history"),
        ("alerts", "serve"),
        ("entry-watch", "serve"),
        ("engine", "patreon-caps"),
        ("alerts", "patreon-caps"),
        ("monitor", "patreon-caps"),
    ):
        result = runner.invoke(app, [*command, "--help"])

        assert result.exit_code == 0
        assert "process" in result.stdout.lower()


def test_live_help_exposes_analysis_only_operation() -> None:
    result = runner.invoke(app, ["live", "--help"])
    output = _plain(result.stdout)

    assert result.exit_code == 0
    assert "analysis-only" in output.lower()
    assert "--once" in output


def test_sec_daily_help_exposes_bounded_filing_scan() -> None:
    result = runner.invoke(app, ["sec", "daily", "--help"])
    output = _plain(result.stdout)

    assert result.exit_code == 0
    assert "filing" in output.lower()
    assert "--lookback-days" in output


def test_peter_lynch_command_runs_once_and_prints_json() -> None:
    summary = {
        "service": "peter-lynch-v1",
        "evaluated": 2,
        "selected": 1,
        "discarded": 1,
        "unsupported": 0,
        "errors": 0,
        "saved": 2,
    }

    async def fake_run(*, progress: Callable[[str], None]) -> dict[str, object]:
        progress("Watchlist: 2 símbolos activos.")
        progress("[1/2] TEST: seleccionado 6/6 (FAST_GROWER).")
        return summary

    with patch("app.integration.peter_lynch_composition.run_peter_lynch_once", new=fake_run):
        result = runner.invoke(app, ["engine", "peter-lynch"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == summary
    assert "[Peter Lynch] Watchlist: 2 símbolos activos." in result.stderr
    assert "[Peter Lynch] [1/2] TEST: seleccionado 6/6" in result.stderr


def test_version_is_available_without_runtime_dependencies() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip().startswith("marketbot ")


def test_placeholder_groups_are_honest_about_availability() -> None:
    for group in ("rules", "strategy", "audit", "infra"):
        result = runner.invoke(app, [group])

        assert result.exit_code == 0
        assert "not installed" in result.stdout.lower()


def test_supervisor_demo_renders_machine_readable_summary(
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        app,
        ["supervisor", "demo", "--price", "15", "--runtime-root", str(tmp_path)],
    )

    assert result.exit_code == 0
    summary = json.loads(result.stdout)
    assert [evaluation["mode"] for evaluation in summary] == ["PRIMARY", "SHADOW"]
    assert summary[0]["eligible"] is True
    assert summary[1]["eligible"] is False
    for evaluation in summary:
        assert evaluation["strategy_definition_hash"].startswith("sha256:")
        assert evaluation["compiled_plan_hash"].startswith("sha256:")
        assert evaluation["registry_snapshot_hash"].startswith("sha256:")
