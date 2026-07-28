import asyncio
import json
from pathlib import Path

from typer.testing import CliRunner

from app.operator_cli.main import _run_async, app

runner = CliRunner()


def test_async_cli_commands_use_selector_event_loop() -> None:
    async def current_loop() -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    loop = _run_async(current_loop())

    assert isinstance(loop, asyncio.SelectorEventLoop)


def test_root_help_lists_operator_groups() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for group in ("rules", "strategy", "audit", "supervisor", "infra", "live", "sec"):
        assert group in result.stdout


def test_live_help_exposes_analysis_only_operation() -> None:
    result = runner.invoke(app, ["live", "--help"])

    assert result.exit_code == 0
    assert "analysis-only" in result.stdout.lower()
    assert "--once" in result.stdout


def test_sec_daily_help_exposes_bounded_filing_scan() -> None:
    result = runner.invoke(app, ["sec", "daily", "--help"])

    assert result.exit_code == 0
    assert "filing" in result.stdout.lower()
    assert "--lookback-days" in result.stdout


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
