import asyncio
import json
import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from app.operator_cli.main import _backtest_simulated_date, _run_async, app

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
        "analyzer",
        "assembly",
        "sec",
        "engine",
        "market",
        "alerts",
        "entry-watch",
        "entry-opportunity",
        "outbox",
        "monitor",
        "connector",
    ):
        assert group in result.stdout


def test_connector_catalog_and_validation_are_available_without_nats() -> None:
    catalog = runner.invoke(app, ["connector", "list-engines"])
    invalid = runner.invoke(app, ["connector", "subscribe"])

    assert catalog.exit_code == 0
    payload = json.loads(catalog.stdout)
    assert payload["swing"] == ["marketbot.v1.analysis.result.SWING.>"]
    assert payload["signal-fusion"]
    assert invalid.exit_code != 0
    assert "at least one engine" in _plain(invalid.output)


def test_connector_help_exposes_position_and_backpressure_controls() -> None:
    result = runner.invoke(app, ["connector", "subscribe", "--help"])
    output = _plain(result.stdout)

    assert result.exit_code == 0
    for option in ("--engine", "--subject", "--start-at", "--durable", "--batch-size"):
        assert option in output


def test_distributed_process_commands_are_explicit() -> None:
    for command in (
        ("engine", "long"),
        ("engine", "swing"),
        ("engine", "swing-channel-4h"),
        ("engine", "4hgeri"),
        ("engine", "intraday"),
        ("market", "stream"),
        ("market", "backtest"),
        ("market", "history"),
        ("alerts", "serve"),
        ("entry-watch", "serve"),
        ("entry-opportunity", "serve"),
        ("outbox", "serve"),
        ("engine", "patreon-caps"),
        ("engine", "entry-recovery"),
        ("alerts", "patreon-caps"),
        ("monitor", "patreon-caps"),
        ("monitor", "entry-opportunity"),
        ("monitor", "swing-channel-4h"),
        ("monitor", "4hgeri"),
        ("monitor", "swing-trade"),
    ):
        result = runner.invoke(app, [*command, "--help"])

        assert result.exit_code == 0
        assert "process" in result.stdout.lower()


def test_market_backtest_parses_isolated_run_configuration() -> None:
    async def fake_backtest(config: object) -> dict[str, object]:
        assert config.source_date == date(2026, 8, 5)
        assert config.source_end_date == date(2026, 8, 7)
        assert config.simulated_date == date(2026, 8, 10)
        assert config.cadence_seconds == 0.25
        assert config.symbols == ("AAPL", "MSFT")
        assert config.default_holding_quantity == Decimal("1")
        assert config.run_id == "research-42"
        assert config.output_path == Path("results/research-42.json")
        return {"events_published": 780, "mode": "backtest"}

    with patch(
        "app.integration.signal_backtest.run_signal_backtest",
        new=fake_backtest,
    ):
        result = runner.invoke(
            app,
            [
                "market",
                "backtest",
                "2026-08-05",
                "--source-end-date",
                "2026-08-07",
                "--simulated-date",
                "2026-08-10",
                "--symbols",
                "AAPL,MSFT",
                "--cadence-seconds",
                "0.25",
                "--run-id",
                "research-42",
                "--output",
                "results/research-42.json",
            ],
        )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "events_published": 780,
        "mode": "backtest",
    }


def test_backtest_default_simulated_date_skips_weekends_and_follows_source() -> None:
    assert _backtest_simulated_date(source=date(2026, 8, 14), today=date(2026, 8, 16)) == date(
        2026, 8, 17
    )
    assert _backtest_simulated_date(source=date(2026, 8, 17), today=date(2026, 8, 17)) == date(
        2026, 8, 18
    )


def test_live_help_exposes_analysis_only_operation() -> None:
    result = runner.invoke(app, ["live", "--help"])
    output = _plain(result.stdout)

    assert result.exit_code == 0
    assert "analysis-only" in output.lower()
    assert "--once" in output


def test_analyzer_passes_the_received_ticker_and_prints_json() -> None:
    summary = {
        "symbol": "TEST",
        "execution_enabled": False,
        "excluded_engines": {
            "peter-lynch": "excluded_by_design_slow_provider",
            "dilution-sec": "excluded_by_design_slow_provider",
        },
        "engines": [],
    }

    async def fake_run(**kwargs: object) -> dict[str, object]:
        assert kwargs == {
            "symbol": "TEST",
            "timeout_seconds": 12.0,
            "runtime_root": Path(".runtime"),
            "mirror_to_nats": False,
        }
        return summary

    with patch(
        "app.integration.symbol_analysis_composition.run_market_analyzer",
        new=fake_run,
    ):
        result = runner.invoke(
            app,
            ["analyzer", "TEST", "--timeout-seconds", "12", "--no-nats"],
        )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == summary


def test_assembly_command_exposes_implementation_strategy_and_mode() -> None:
    result = runner.invoke(app, ["assembly"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["version"] == "7.27.0"
    assert payload["engines"]["swing"]["implementation"] == "11.0.0"
    assert payload["engines"]["swing"]["strategy"]["version"] == "3.3.0"
    assert payload["engines"]["swing-channel-4h"]["implementation"] == "1.1.0"
    assert payload["engines"]["4hgeri"]["implementation"] == "1.5.0"
    assert payload["engines"]["entry-watcher"]["implementation"] == "5.5.0"
    assert payload["engines"]["entry-opportunity"]["implementation"] == "5.0.0"
    assert payload["engines"]["swing-trade"]["implementation"] == "1.2.0"
    assert payload["engines"]["intraday"]["implementation"] == "4.0.0"
    assert payload["engines"]["portfolio-flow"]["strategy"]["version"] == "2.0.0"
    assert payload["engines"]["options-gamma"]["mode"] == "active"
    assert payload["engines"]["patreon-caps"]["mode"] == "on-demand"
    assert payload["engines"]["elliott-wave"]["mode"] == "on-demand"
    assert payload["engines"]["support-confirmation"]["mode"] == "active"
    assert payload["engines"]["signal-fusion"]["mode"] == "on-demand"
    assert payload["engines"]["peter-lynch"]["mode"] == "on-demand"


def test_runtime_slots_command_reads_active_modes_from_the_definition() -> None:
    result = runner.invoke(app, ["runtime-slots", "--mode", "active"])

    assert result.exit_code == 0
    slots = result.stdout.splitlines()
    assert "entry-recovery" in slots
    assert "signal-fusion" not in slots
    assert "dilution-sec" not in slots
    assert "peter-lynch" not in slots


def test_runtime_plan_command_exposes_commands_and_dependency_batches() -> None:
    result = runner.invoke(
        app,
        [
            "runtime-plan",
            "--runtime-root",
            "C:/runtime root",
            "--symbols",
            "HIMS,ZETA",
            "--no-bell",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["definition_version"] == "7.27.0"
    assert payload["startup_batches"][0] == ["outbox-relay"]
    processes = {item["name"]: item for item in payload["processes"]}
    assert processes["confirmed-buy-monitor"]["operator_monitor"] is True
    assert processes["confirmed-buy-monitor"]["dependencies"] == ["alert"]
    assert processes["long-term"]["arguments"][-2:] == ["--symbols", "HIMS,ZETA"]
    assert processes["swing-channel-4h"]["arguments"][-2:] == [
        "--symbols",
        "HIMS,ZETA",
    ]
    assert processes["4hgeri"]["arguments"][-2:] == ["--symbols", "HIMS,ZETA"]
    assert processes["4hgeri"]["dependencies"] == [
        "market-history-v1",
        "support-confirmation-v0",
    ]
    assert processes["swing-trade"]["dependencies"] == [
        "market-history-v1",
        "4hgeri",
        "entry-opportunity",
        "support-confirmation-v0",
    ]


def test_single_dash_analyzer_alias_passes_the_received_ticker() -> None:
    summary = {"symbol": "NVDA", "engines": []}

    with patch("app.operator_cli.main._run_market_analyzer", return_value=summary) as run:
        result = runner.invoke(app, ["-analyzer", "NVDA"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == summary
    run.assert_called_once_with("NVDA")


def test_sec_daily_help_exposes_bounded_filing_scan() -> None:
    result = runner.invoke(app, ["sec", "daily", "--help"])
    output = _plain(result.stdout)

    assert result.exit_code == 0
    assert "filing" in output.lower()
    assert "--lookback-days" in output


def test_sec_daily_accepts_ninety_day_lookback() -> None:
    received: dict[str, object] = {}

    async def fake_run_sec_daily_analysis(**kwargs: object) -> dict[str, object]:
        received.update(kwargs)
        return {"symbols_scanned": 1}

    with patch(
        "app.integration.sec_daily_composition.run_sec_daily_analysis",
        new=fake_run_sec_daily_analysis,
    ):
        result = runner.invoke(
            app,
            [
                "sec",
                "daily",
                "--lookback-days",
                "90",
                "--symbols",
                "ADUR",
                "--no-nats",
            ],
        )

    assert result.exit_code == 0
    assert received["lookback_days"] == 90


def test_sec_snapshot_requests_analysis_without_recent_filings() -> None:
    received: dict[str, object] = {}

    async def fake_run_sec_daily_analysis(**kwargs: object) -> dict[str, object]:
        received.update(kwargs)
        return {"symbols_scanned": 1, "analyses_published": 1}

    with patch(
        "app.integration.sec_daily_composition.run_sec_daily_analysis",
        new=fake_run_sec_daily_analysis,
    ):
        result = runner.invoke(
            app,
            [
                "sec",
                "snapshot",
                "--lookback-days",
                "90",
                "--symbols",
                "ADUR",
                "--no-nats",
            ],
        )

    assert result.exit_code == 0
    assert received["scan_mode"] == "snapshot"


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

    received: dict[str, object] = {}

    async def fake_run(
        *, progress: Callable[[str], None], analysis_ttl_days: int | None = None
    ) -> dict[str, object]:
        received["analysis_ttl_days"] = analysis_ttl_days
        progress("Watchlist: 2 símbolos activos.")
        progress("[1/2] TEST: seleccionado 6/6 (FAST_GROWER).")
        return summary

    with patch("app.integration.peter_lynch_composition.run_peter_lynch_once", new=fake_run):
        result = runner.invoke(app, ["engine", "peter-lynch", "--ttl-days", "30"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == summary
    assert received["analysis_ttl_days"] == 30
    assert "[Peter Lynch] Watchlist: 2 símbolos activos." in result.stderr
    assert "[Peter Lynch] [1/2] TEST: seleccionado 6/6" in result.stderr


def test_version_is_available_without_runtime_dependencies() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip().startswith("marketbot ")


def test_entry_opportunity_history_cleanup_defaults_to_dry_run() -> None:
    async def fake_maintain(**kwargs: object) -> dict[str, object]:
        assert kwargs["apply"] is False
        assert kwargs["retain_per_opportunity"] == 100
        assert kwargs["batch_size"] == 1000
        return {
            "applied": False,
            "candidate_rows": 2500,
            "candidate_bytes": 75_000_000,
            "deleted_rows": 0,
        }

    with patch(
        "app.integration.entry_opportunity_history_maintenance.maintain_entry_opportunity_history",
        new=fake_maintain,
    ):
        result = runner.invoke(
            app,
            ["entry-opportunity", "prune-history", "--older-than-days", "30"],
        )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["applied"] is False


def test_entry_opportunity_audit_defaults_to_human_output() -> None:
    report = {
        "evidence_audit": {
            "sample": {
                "opportunities": 1,
                "checkpoints": 1,
                "tracking_references": 1,
                "actionable_entries": 0,
                "open_checkpoints": 1,
                "closed_checkpoints": 0,
            },
            "snapshot": {
                "tracking": {
                    "observed": 1,
                    "positive": 0,
                    "negative": 1,
                    "breakeven": 0,
                    "average_percent": "-1.0000",
                    "median_percent": "-1.0000",
                },
                "actionable": {
                    "observed": 0,
                    "positive": 0,
                    "negative": 0,
                    "breakeven": 0,
                    "average_percent": None,
                    "median_percent": None,
                },
            },
            "fixed_horizons": {
                role: {
                    horizon: {
                        "observed": 0,
                        "positive": 0,
                        "negative": 0,
                        "average_percent": None,
                    }
                    for horizon in ("15m", "30m", "60m", "close")
                }
                for role in ("tracking", "actionable")
            },
            "negative_evidence": [],
            "pullback_entry_improvement": [],
            "limitations": ["muestra abierta"],
        }
    }

    async def fake_load(*, history: int) -> dict[str, object]:
        assert history == 5000
        return report

    with patch(
        "app.integration.entry_opportunity_report.load_entry_opportunity_report",
        new=fake_load,
    ):
        result = runner.invoke(app, ["entry-opportunity", "audit"])

    assert result.exit_code == 0
    assert "REFERENCIAS (NO SON COMPRAS)" in result.stdout
    assert "ENTRADAS ACCIONABLES L1-L4" in result.stdout


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
    assert [evaluation["mode"] for evaluation in summary] == ["PRIMARY", "CANDIDATE"]
    assert summary[0]["eligible"] is True
    assert summary[1]["eligible"] is False
    for evaluation in summary:
        assert evaluation["strategy_definition_hash"].startswith("sha256:")
        assert evaluation["compiled_plan_hash"].startswith("sha256:")
        assert evaluation["registry_snapshot_hash"].startswith("sha256:")
