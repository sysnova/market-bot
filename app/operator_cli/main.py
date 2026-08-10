"""Top-level MarketBot operator CLI."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

import typer

from app import __version__
from app.common.clock import SystemClock
from app.contracts import EventEnvelope, MarketSession
from app.event_bus import InMemoryEventBus
from app.integration.foundation import prepare_foundation_engine

from .async_support import run_async as _run_async
from .infrastructure_commands import register_infrastructure_commands
from .runtime_commands import register_runtime_commands

_GROUPS: tuple[tuple[str, str], ...] = (
    ("rules", "Inspect and manage trading rules."),
    ("strategy", "Inspect and manage strategy engines."),
    ("audit", "Query the audit trail."),
    ("infra", "Inspect infrastructure connectivity."),
)

app = typer.Typer(
    name="marketbot",
    help="Operate a MarketBot deployment.",
    no_args_is_help=True,
    invoke_without_command=True,
    pretty_exceptions_enable=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"marketbot {__version__}")
        raise typer.Exit


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="Show the version."
        ),
    ] = False,
    analyzer: Annotated[
        str | None,
        typer.Option(
            "-analyzer",
            help="Analyze one ticker through every engine except Peter Lynch and SEC.",
        ),
    ] = None,
) -> None:
    """Operate a MarketBot deployment."""

    if analyzer is not None:
        typer.echo(json.dumps(_run_market_analyzer(analyzer), indent=2, sort_keys=True))
        raise typer.Exit


def _run_market_analyzer(
    symbol: str,
    *,
    timeout_seconds: float = 30.0,
    runtime_root: Path = Path(".runtime"),
    mirror_to_nats: bool = True,
) -> dict[str, object]:
    from app.integration.symbol_analysis_composition import run_market_analyzer

    return _run_async(
        run_market_analyzer(
            symbol=symbol,
            timeout_seconds=timeout_seconds,
            runtime_root=runtime_root,
            mirror_to_nats=mirror_to_nats,
        )
    )


def _iso_date(value: str, *, option_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise typer.BadParameter(
            f"{option_name} must use YYYY-MM-DD",
        ) from error


def _placeholder(name: str, help_text: str) -> typer.Typer:
    group = typer.Typer(name=name, help=help_text, invoke_without_command=True)

    def unavailable() -> None:
        typer.echo(f"The {name} operator module is not installed.")

    group.callback()(unavailable)
    return group


for group_name, group_help in _GROUPS:
    app.add_typer(_placeholder(group_name, group_help), name=group_name)

register_runtime_commands(app)
register_infrastructure_commands(app)


@app.command("analyzer")
def analyzer_symbol(
    symbol: Annotated[str, typer.Argument(help="Single market symbol to analyze.")],
    timeout_seconds: Annotated[
        float,
        typer.Option(min=1, max=300, help="Maximum seconds allowed per engine."),
    ] = 30.0,
    runtime_root: Annotated[
        Path,
        typer.Option(help="Directory for local append-only analytical artifacts."),
    ] = Path(".runtime"),
    nats: Annotated[
        bool,
        typer.Option("--nats/--no-nats", help="Publish current results for downstream engines."),
    ] = True,
) -> None:
    """Analyze one symbol through every engine except Peter Lynch and SEC."""

    summary = _run_market_analyzer(
        symbol,
        timeout_seconds=timeout_seconds,
        runtime_root=runtime_root,
        mirror_to_nats=nats,
    )
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))


@app.command("live")
def live_analysis(
    once: Annotated[
        bool,
        typer.Option(help="Warm data, evaluate once, and exit without opening the stream."),
    ] = False,
    runtime_root: Annotated[
        Path,
        typer.Option(help="Directory for local append-only alerts."),
    ] = Path(".runtime"),
    bell: Annotated[
        bool,
        typer.Option(help="Ring only for explicit L1-L4 buy alerts."),
    ] = True,
    nats: Annotated[
        bool,
        typer.Option("--nats/--no-nats", help="Mirror events durably to local NATS."),
    ] = True,
    symbols: Annotated[
        str | None,
        typer.Option(
            help="Comma-separated temporary universe; overrides local PostgreSQL for this run."
        ),
    ] = None,
) -> None:
    """Run the realtime analysis-only bot; this command cannot submit orders."""

    from app.integration.live_composition import run_live_analysis

    summary = _run_async(
        run_live_analysis(
            once=once,
            runtime_root=runtime_root,
            bell=bell,
            mirror_to_nats=nats,
            symbols=tuple(symbols.split(",")) if symbols else None,
        )
    )
    if summary is not None:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))


engine = typer.Typer(name="engine", help="Run one independent analytical engine process.")
app.add_typer(engine, name="engine")


def _engine_process(
    horizon: str,
    *,
    once: bool,
    symbols: str | None,
    ready_path: Path,
) -> None:
    from app.contracts import AnalysisHorizon
    from app.integration.distributed_composition import run_engine_process

    summary = _run_async(
        run_engine_process(
            horizon=AnalysisHorizon(horizon),
            symbols=tuple(symbols.split(",")) if symbols else None,
            once=once,
            ready_path=ready_path,
        )
    )
    if summary is not None:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))


@engine.command("long")
def long_engine_process(
    once: Annotated[bool, typer.Option(help="Bootstrap and evaluate once, then exit.")] = False,
    symbols: Annotated[
        str | None,
        typer.Option(help="Comma-separated temporary universe; overrides local PostgreSQL."),
    ] = None,
    ready_path: Annotated[
        Path,
        typer.Option(help="Readiness file written after this process subscribes to NATS."),
    ] = Path(".runtime/status/long-term.ready.json"),
) -> None:
    """Run the independently configured Long process."""

    _engine_process("LONG_TERM", once=once, symbols=symbols, ready_path=ready_path)


@engine.command("swing")
def swing_engine_process(
    once: Annotated[bool, typer.Option(help="Bootstrap and evaluate once, then exit.")] = False,
    symbols: Annotated[
        str | None,
        typer.Option(help="Comma-separated temporary universe; overrides PostgreSQL."),
    ] = None,
    ready_path: Annotated[
        Path,
        typer.Option(help="Readiness file written after this process subscribes to NATS."),
    ] = Path(".runtime/status/swing.ready.json"),
) -> None:
    """Run the independently configured Swing process."""

    _engine_process("SWING", once=once, symbols=symbols, ready_path=ready_path)


@engine.command("intraday")
def intraday_engine_process(
    once: Annotated[bool, typer.Option(help="Bootstrap and evaluate once, then exit.")] = False,
    symbols: Annotated[
        str | None,
        typer.Option(help="Comma-separated temporary universe; overrides PostgreSQL."),
    ] = None,
    ready_path: Annotated[
        Path,
        typer.Option(help="Readiness file written after this process subscribes to NATS."),
    ] = Path(".runtime/status/intraday.ready.json"),
) -> None:
    """Run the independently configured Intraday process."""

    _engine_process("INTRADAY", once=once, symbols=symbols, ready_path=ready_path)


@engine.command("rotation")
def rotation_engine_process(
    once: Annotated[bool, typer.Option(help="Analizar una vez y salir.")] = False,
    interval_minutes: Annotated[
        int, typer.Option(min=1, max=1440, help="Frecuencia del análisis.")
    ] = 5,
    ready_path: Annotated[Path, typer.Option(help="Archivo de readiness del proceso.")] = Path(
        ".runtime/status/market-rotation-v1.ready.json"
    ),
) -> None:
    """Monitorea rotación sectorial, persiste ROT y publica el reporte en NATS."""
    from app.integration.market_rotation_composition import run_market_rotation_process

    summary = _run_async(
        run_market_rotation_process(
            once=once, interval_minutes=interval_minutes, ready_path=ready_path
        )
    )
    if summary is not None:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))


@engine.command("peter-lynch")
def peter_lynch_engine_process() -> None:
    """Evaluate the active watchlist once with the Peter Lynch fundamental screen."""

    from app.integration.peter_lynch_composition import run_peter_lynch_once

    def report(message: str) -> None:
        typer.echo(f"[Peter Lynch] {message}", err=True)

    summary = _run_async(run_peter_lynch_once(progress=report))
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))


@engine.command("portfolio-flow")
def portfolio_flow_process(
    ready_path: Annotated[Path, typer.Option(help="Archivo de readiness del proceso.")] = Path(
        ".runtime/status/portfolio-flow-v1.ready.json"
    ),
) -> None:
    """Monitorea order flow efímero sólo para posiciones abiertas."""
    from app.integration.portfolio_flow_composition import run_portfolio_flow_process

    _run_async(run_portfolio_flow_process(ready_path=ready_path))


@engine.command("long-portfolio")
def long_portfolio_process(
    config_path: Annotated[
        Path | None,
        typer.Option(help="Optional strategy override; defaults to the MarketBot assembly."),
    ] = None,
    runtime_root: Annotated[
        Path, typer.Option(help="Directory for the deduplicated LONG alert ledger.")
    ] = Path(".runtime"),
    ready_path: Annotated[
        Path, typer.Option(help="Readiness file written after subscribing to NATS.")
    ] = Path(".runtime/status/long-portfolio-v1.ready.json"),
    once: Annotated[bool, typer.Option(help="Replay the requested symbol once and exit.")] = False,
    symbol: Annotated[
        str | None,
        typer.Option(help="Optional single PORT_YTD symbol for one-shot analysis."),
    ] = None,
) -> None:
    """Monitor solid, allocation-aware entries for the year-end LONG portfolio."""

    from app.integration.long_portfolio_composition import run_long_portfolio_process

    summary = _run_async(
        run_long_portfolio_process(
            config_path=config_path,
            runtime_root=runtime_root,
            ready_path=ready_path,
            once=once,
            symbol=symbol,
        )
    )
    if summary is not None:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))


@engine.command("patreon-caps")
def patreon_caps_process(
    config_path: Annotated[
        Path | None,
        typer.Option(help="Optional strategy override; defaults to the MarketBot assembly."),
    ] = None,
    ready_path: Annotated[
        Path, typer.Option(help="Readiness file written after NATS and PostgreSQL are ready.")
    ] = Path(".runtime/status/patreon-caps-v1.ready.json"),
    once: Annotated[bool, typer.Option(help="Hydrate, evaluate once, and exit.")] = False,
) -> None:
    """Run the independent PatreonCaps v1 analytical process."""

    from app.integration.patreon_caps_composition import run_patreon_caps_process

    summary = _run_async(
        run_patreon_caps_process(
            config_path=config_path,
            ready_path=ready_path,
            once=once,
        )
    )
    if summary is not None:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))


@engine.command("elliott-wave")
def elliott_wave_process(
    once: Annotated[bool, typer.Option(help="Analyze held positions once and exit.")] = False,
    ready_path: Annotated[
        Path, typer.Option(help="Readiness file written after NATS and holdings are ready.")
    ] = Path(".runtime/status/elliott-wave-v0.ready.json"),
) -> None:
    """Run Elliott Wave analysis only for positive local holdings."""

    from app.integration.elliott_wave_composition import run_elliott_wave_process

    summary = _run_async(run_elliott_wave_process(ready_path=ready_path, once=once))
    if summary is not None:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))


@engine.command("support-confirmation")
def support_confirmation_process(
    once: Annotated[bool, typer.Option(help="Analyze held positions once and exit.")] = False,
    ready_path: Annotated[
        Path, typer.Option(help="Readiness file written after NATS and holdings are ready.")
    ] = Path(".runtime/status/support-confirmation-v0.ready.json"),
) -> None:
    """Run independent support confirmation for positive local holdings."""

    from app.integration.support_confirmation_composition import (
        run_support_confirmation_process,
    )

    summary = _run_async(run_support_confirmation_process(ready_path=ready_path, once=once))
    if summary is not None:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))


@engine.command("signal-fusion")
def signal_fusion_process(
    once: Annotated[bool, typer.Option(help="Fuse the latest held-position inputs once.")] = False,
    ready_path: Annotated[
        Path, typer.Option(help="Readiness file written after source replay is complete.")
    ] = Path(".runtime/status/signal-fusion-v0.ready.json"),
) -> None:
    """Run holdings-only cross-engine analytical fusion."""

    from app.integration.signal_fusion_composition import run_signal_fusion_process

    summary = _run_async(run_signal_fusion_process(ready_path=ready_path, once=once))
    if summary is not None:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))


@engine.command("entry-recovery")
def entry_recovery_process(
    ready_path: Annotated[
        Path,
        typer.Option(help="Readiness file written after PostgreSQL and NATS are ready."),
    ] = Path(".runtime/status/entry-recovery.ready.json"),
) -> None:
    """Run the independent paper-entry recovery process."""

    from app.integration.entry_recovery_composition import run_entry_recovery_process

    _run_async(run_entry_recovery_process(ready_path=ready_path))


market = typer.Typer(name="market", help="Run independent market-data processes.")
app.add_typer(market, name="market")

@market.command("stream")
def market_stream_process(
    symbols: Annotated[
        str | None,
        typer.Option(help="Comma-separated temporary universe; overrides PostgreSQL."),
    ] = None,
) -> None:
    """Run the Alpaca WebSocket-to-NATS process."""

    from app.integration.distributed_composition import run_market_stream_process

    _run_async(
        run_market_stream_process(
            symbols=tuple(symbols.split(",")) if symbols else None,
        )
    )


@market.command("backtest")
def market_backtest_process(
    source_date: Annotated[
        str,
        typer.Argument(help="Historical NY market date to replay (YYYY-MM-DD)."),
    ],
    symbols: Annotated[
        str,
        typer.Option(help="Required comma-separated symbols to evaluate."),
    ],
    simulated_date: Annotated[
        str | None,
        typer.Option(help="Date exposed to engines; defaults to today in New York."),
    ] = None,
    cadence_seconds: Annotated[
        float,
        typer.Option(min=0, help="Real seconds between successive market-minute bars."),
    ] = 0,
    default_quantity: Annotated[
        str,
        typer.Option(help="Simulated holding quantity assigned to every input symbol."),
    ] = "1",
    run_id: Annotated[
        str | None,
        typer.Option(help="Optional run identity; generated automatically when omitted."),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(help="JSON result artifact; defaults under .runtime/backtests."),
    ] = None,
) -> None:
    """Run a buy-signal backtesting process without operational NATS or PostgreSQL."""

    from app.integration.signal_backtest import SignalBacktestConfig, run_signal_backtest

    parsed_source = _iso_date(source_date, option_name="source date")
    target_date = (
        _iso_date(simulated_date, option_name="--simulated-date")
        if simulated_date is not None
        else SystemClock().now().astimezone(ZoneInfo("America/New_York")).date()
    )
    try:
        quantity = Decimal(default_quantity)
    except InvalidOperation as error:
        raise typer.BadParameter(
            "must be a positive decimal", param_hint="--default-quantity"
        ) from error
    normalized_symbols = tuple(symbols.split(","))
    resolved_run_id = run_id or f"backtest-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
    output_path = output or Path(".runtime/backtests") / f"{resolved_run_id}.json"
    summary = _run_async(
        run_signal_backtest(
            SignalBacktestConfig(
                source_date=parsed_source,
                simulated_date=target_date,
                cadence_seconds=cadence_seconds,
                symbols=normalized_symbols,
                default_holding_quantity=quantity,
                run_id=resolved_run_id,
                output_path=output_path,
            )
        )
    )
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))


@market.command("history")
def market_history_process(
    ready_path: Annotated[
        Path,
        typer.Option(help="Readiness file written after the NATS RPC subscription exists."),
    ] = Path(".runtime/status/market-history-v1.ready.json"),
) -> None:
    """Run the centralized incremental Alpaca REST history process."""

    from app.integration.market_history_composition import run_market_history_process

    _run_async(run_market_history_process(ready_path=ready_path))


alerts = typer.Typer(name="alerts", help="Run the independent alert aggregation process.")
app.add_typer(alerts, name="alerts")


@alerts.command("serve")
def alert_process(
    runtime_root: Annotated[
        Path,
        typer.Option(help="Directory for local append-only alerts."),
    ] = Path(".runtime"),
    bell: Annotated[
        bool,
        typer.Option(help="Ring only for explicit L1-L4 buy alerts."),
    ] = True,
    ready_path: Annotated[
        Path,
        typer.Option(help="Readiness file written after all NATS subscriptions exist."),
    ] = Path(".runtime/status/alert.ready.json"),
) -> None:
    """Run the configured Alert NATS consumer process."""

    from app.integration.distributed_composition import run_alert_process

    _run_async(
        run_alert_process(
            runtime_root=runtime_root,
            bell=bell,
            ready_path=ready_path,
        )
    )


@alerts.command("confirmed")
def confirmed_buy_monitor(
    bell: Annotated[
        bool,
        typer.Option(help="Play the native pattern for each L1-L4 buy alert."),
    ] = True,
    ready_path: Annotated[
        Path,
        typer.Option(help="Readiness file written after subscribing to NATS."),
    ] = Path(".runtime/status/confirmed-buy-monitor.ready.json"),
) -> None:
    """Show solid buys and portfolio-protection events received through NATS."""

    from app.integration.confirmed_buy_monitor import run_confirmed_buy_monitor

    _run_async(run_confirmed_buy_monitor(ready_path=ready_path, bell=bell))


@alerts.command("long-portfolio")
def long_portfolio_monitor(
    bell: Annotated[bool, typer.Option(help="Ring for each new LONG portfolio alert.")] = True,
    history: Annotated[
        int, typer.Option(min=1, max=500, help="Persisted PostgreSQL alerts shown on startup.")
    ] = 25,
    progress_minutes: Annotated[
        int,
        typer.Option(
            min=1,
            max=1440,
            help="Minutes between per-symbol LONG validation progress snapshots.",
        ),
    ] = 60,
    ready_path: Annotated[
        Path, typer.Option(help="Readiness file written after history and NATS are ready.")
    ] = Path(".runtime/status/long-portfolio-monitor.ready.json"),
) -> None:
    """Show only persisted and live year-end LONG portfolio alerts."""

    from app.integration.long_portfolio_monitor import run_long_portfolio_monitor

    _run_async(
        run_long_portfolio_monitor(
            ready_path=ready_path,
            bell=bell,
            history=history,
            progress_interval=timedelta(minutes=progress_minutes),
        )
    )


@alerts.command("patreon-caps")
def patreon_caps_alert_monitor(
    bell: Annotated[bool, typer.Option(help="Ring only for PatreonCaps BUY events.")] = True,
    history: Annotated[
        int, typer.Option(min=1, max=500, help="Persisted transitions shown on startup.")
    ] = 50,
    ready_path: Annotated[
        Path, typer.Option(help="Readiness file written after history and NATS are ready.")
    ] = Path(".runtime/status/patreon-caps-alerts.ready.json"),
) -> None:
    """Run the persisted and live PatreonCaps alert process."""

    from app.integration.patreon_caps_monitor import run_patreon_caps_monitor

    _run_async(
        run_patreon_caps_monitor(
            mode="alerts",
            ready_path=ready_path,
            history=history,
            bell=bell,
        )
    )


monitor = typer.Typer(name="monitor", help="Run dedicated analytical terminal views.")
app.add_typer(monitor, name="monitor")


@monitor.command("patreon-caps")
def patreon_caps_analysis_monitor(
    ready_path: Annotated[
        Path, typer.Option(help="Readiness file written after subscribing to NATS.")
    ] = Path(".runtime/status/patreon-caps-analysis.ready.json"),
) -> None:
    """Run the live PatreonCaps calculation monitor process."""

    from app.integration.patreon_caps_monitor import run_patreon_caps_monitor

    _run_async(
        run_patreon_caps_monitor(
            mode="analysis",
            ready_path=ready_path,
            bell=False,
        )
    )


@monitor.command("elliott-wave")
def elliott_wave_analysis_monitor(
    ready_path: Annotated[
        Path, typer.Option(help="Readiness file written after subscribing to NATS.")
    ] = Path(".runtime/status/elliott-wave-analysis.ready.json"),
) -> None:
    """Show live Elliott Wave assessments for held positions."""

    from app.integration.elliott_wave_monitor import run_elliott_wave_monitor

    _run_async(run_elliott_wave_monitor(ready_path=ready_path))


@monitor.command("support-confirmation")
def support_confirmation_monitor(
    bell: Annotated[
        bool, typer.Option(help="Ring for new structurally confirmed reentries.")
    ] = True,
    ready_path: Annotated[
        Path, typer.Option(help="Readiness file written after subscribing to NATS.")
    ] = Path(".runtime/status/support-confirmation-analysis.ready.json"),
) -> None:
    """Show support-reaction and reversal evidence for held positions."""

    from app.integration.support_confirmation_monitor import (
        run_support_confirmation_monitor,
    )

    _run_async(run_support_confirmation_monitor(ready_path=ready_path, bell=bell))


@monitor.command("signal-fusion")
def signal_fusion_monitor(
    mode: Annotated[
        Literal["analysis", "buys"],
        typer.Option(help="Show every decision or only current confirmed buys."),
    ] = "analysis",
    bell: Annotated[bool, typer.Option(help="Ring for new confirmed buys.")] = True,
    ready_path: Annotated[
        Path, typer.Option(help="Readiness file written after replay is complete.")
    ] = Path(".runtime/status/signal-fusion-analysis.ready.json"),
) -> None:
    """Show Signal Fusion evidence or current analytical buy confirmations."""

    from app.integration.signal_fusion_monitor import run_signal_fusion_monitor

    _run_async(
        run_signal_fusion_monitor(
            mode=mode,
            ready_path=ready_path,
            bell=bell,
        )
    )


@monitor.command("entry-opportunity")
def entry_opportunity_monitor(
    history: Annotated[
        int,
        typer.Option(min=1, max=1000, help="Recent opportunities displayed in the panel."),
    ] = 100,
    refresh_seconds: Annotated[
        int,
        typer.Option(
            min=5,
            max=3600,
            help="PostgreSQL fallback refresh interval in seconds.",
        ),
    ] = 30,
    ready_path: Annotated[
        Path, typer.Option(help="Readiness file written after PostgreSQL and NATS are ready.")
    ] = Path(".runtime/status/entry-opportunity-monitor.ready.json"),
) -> None:
    """Run the event-driven Entry Opportunity tracking panel process."""

    from app.integration.entry_opportunity_monitor import run_entry_opportunity_monitor

    _run_async(
        run_entry_opportunity_monitor(
            ready_path=ready_path,
            history=history,
            refresh_interval=timedelta(seconds=refresh_seconds),
        )
    )


entry_watch = typer.Typer(
    name="entry-watch",
    help="Run the independent Entry Watcher detector process.",
)
app.add_typer(entry_watch, name="entry-watch")


@entry_watch.command("serve")
def entry_watcher_process(
    ready_path: Annotated[
        Path,
        typer.Option(help="Readiness file written after PostgreSQL and NATS are ready."),
    ] = Path(".runtime/status/entry-watcher.ready.json"),
) -> None:
    """Run the configured Entry Watcher PostgreSQL/NATS detector process."""

    from app.integration.distributed_composition import run_entry_watcher_process

    _run_async(run_entry_watcher_process(ready_path=ready_path))


@entry_watch.command("report")
def entry_watcher_report(
    history: Annotated[
        int,
        typer.Option(
            min=1,
            max=10000,
            help="Recent opportunities included in L1-L4 and horizon statistics.",
        ),
    ] = 5000,
) -> None:
    """Show open paper trades, maturity progress and audited success rates."""

    from app.integration.entry_opportunity_report import load_entry_opportunity_report

    report = _run_async(load_entry_opportunity_report(history=history))
    typer.echo(json.dumps(report, indent=2, sort_keys=True))


entry_opportunity = typer.Typer(
    name="entry-opportunity",
    help="Run and inspect the independent paper-opportunity lifecycle engine.",
)
app.add_typer(entry_opportunity, name="entry-opportunity")


@entry_opportunity.command("serve")
def entry_opportunity_process(
    ready_path: Annotated[
        Path,
        typer.Option(help="Readiness file written after PostgreSQL and NATS are ready."),
    ] = Path(".runtime/status/entry-opportunity.ready.json"),
) -> None:
    """Run the configured Entry Opportunity PostgreSQL/NATS process."""

    from app.integration.distributed_composition import run_entry_opportunity_process

    _run_async(run_entry_opportunity_process(ready_path=ready_path))


outbox = typer.Typer(
    name="outbox",
    help="Relay committed PostgreSQL outbox events to NATS JetStream.",
)
app.add_typer(outbox, name="outbox")


@outbox.command("serve")
def outbox_relay_process(
    ready_path: Annotated[
        Path,
        typer.Option(help="Readiness file written after PostgreSQL and NATS are ready."),
    ] = Path(".runtime/status/outbox-relay.ready.json"),
) -> None:
    """Run the independent transactional outbox relay process."""

    from app.integration.distributed_composition import run_outbox_relay_process

    _run_async(run_outbox_relay_process(ready_path=ready_path))


@entry_opportunity.command("report")
def entry_opportunity_report(
    history: Annotated[
        int,
        typer.Option(
            min=1,
            max=10000,
            help="Recent opportunities included in L1-L4 and horizon statistics.",
        ),
    ] = 5000,
) -> None:
    """Show open paper trades, maturity progress and audited success rates."""

    from app.integration.entry_opportunity_report import load_entry_opportunity_report

    report = _run_async(load_entry_opportunity_report(history=history))
    typer.echo(json.dumps(report, indent=2, sort_keys=True))


@entry_opportunity.command("prune-history")
def entry_opportunity_prune_history(
    older_than_days: Annotated[
        int,
        typer.Option(
            min=1,
            max=3650,
            help="Only consider legacy evidence events older than this many days.",
        ),
    ] = 30,
    retain_per_opportunity: Annotated[
        int,
        typer.Option(
            min=1,
            max=10000,
            help="Always preserve at least this many newest events per opportunity.",
        ),
    ] = 100,
    batch_size: Annotated[
        int,
        typer.Option(min=1, max=10000, help="Maximum rows deleted per transaction."),
    ] = 1000,
    apply: Annotated[
        bool,
        typer.Option(
            "--apply/--dry-run",
            help="Delete candidates; defaults to a read-only preview.",
        ),
    ] = False,
) -> None:
    """Preview or prune only legacy non-material Opportunity evidence snapshots."""

    from app.integration.entry_opportunity_history_maintenance import (
        maintain_entry_opportunity_history,
    )

    report = _run_async(
        maintain_entry_opportunity_history(
            cutoff=datetime.now(UTC) - timedelta(days=older_than_days),
            retain_per_opportunity=retain_per_opportunity,
            batch_size=batch_size,
            apply=apply,
        )
    )
    typer.echo(json.dumps(report, indent=2, sort_keys=True))


sec = typer.Typer(name="sec", help="Run the independent bounded SEC filing bot.")
app.add_typer(sec, name="sec")


@sec.command("daily")
def sec_daily(
    lookback_days: Annotated[
        int | None,
        typer.Option(
            min=1,
            max=90,
            help="Inclusive recent filing-date window; no historical form backfill.",
        ),
    ] = None,
    runtime_root: Annotated[
        Path,
        typer.Option(help="Directory for local append-only alerts."),
    ] = Path(".runtime"),
    bell: Annotated[
        bool,
        typer.Option(help="Ring the terminal bell for each SEC warning."),
    ] = False,
    nats: Annotated[
        bool,
        typer.Option("--nats/--no-nats", help="Mirror SEC results to local NATS."),
    ] = True,
    symbols: Annotated[
        str | None,
        typer.Option(
            help="Comma-separated temporary universe; overrides local PostgreSQL for this scan."
        ),
    ] = None,
) -> None:
    """Scan recent dilution-related SEC filings once and exit."""

    from app.integration.sec_daily_composition import run_sec_daily_analysis

    summary = _run_async(
        run_sec_daily_analysis(
            runtime_root=runtime_root,
            mirror_to_nats=nats,
            bell=bell,
            lookback_days=lookback_days,
            symbols=tuple(symbols.split(",")) if symbols else None,
            scan_mode="daily",
        )
    )
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))


@sec.command("snapshot")
def sec_snapshot(
    lookback_days: Annotated[
        int | None,
        typer.Option(
            min=1,
            max=90,
            help="Inclusive filing-date window; capped to recent SEC metadata.",
        ),
    ] = None,
    runtime_root: Annotated[
        Path,
        typer.Option(help="Directory for local append-only alerts."),
    ] = Path(".runtime"),
    bell: Annotated[
        bool,
        typer.Option(help="Ring the terminal bell for each SEC warning."),
    ] = False,
    nats: Annotated[
        bool,
        typer.Option("--nats/--no-nats", help="Mirror SEC results to local NATS."),
    ] = True,
    symbols: Annotated[
        str | None,
        typer.Option(
            help="Comma-separated temporary universe; overrides local PostgreSQL for this scan."
        ),
    ] = None,
) -> None:
    """Evaluate CompanyFacts for every symbol plus matching recent filings."""

    from app.integration.sec_daily_composition import run_sec_daily_analysis

    summary = _run_async(
        run_sec_daily_analysis(
            runtime_root=runtime_root,
            mirror_to_nats=nats,
            bell=bell,
            lookback_days=lookback_days,
            symbols=tuple(symbols.split(",")) if symbols else None,
            scan_mode="snapshot",
        )
    )
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))


supervisor = typer.Typer(name="supervisor", help="Run and inspect local engine supervision.")
app.add_typer(supervisor, name="supervisor")


@supervisor.command("demo")
def supervisor_demo(
    price: Annotated[
        int,
        typer.Option(min=1, help="Exact synthetic integer input price."),
    ] = 12,
    runtime_root: Annotated[
        Path,
        typer.Option(help="Directory for append-only audit output."),
    ] = Path("runtime"),
) -> None:
    """Execute PRIMARY v1 and CANDIDATE v2 once in the local in-process supervisor."""

    summary = _run_async(_run_demo(price=price, runtime_root=runtime_root))
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))


async def _run_demo(*, price: int, runtime_root: Path) -> list[dict[str, object]]:
    clock = SystemClock()
    engine, audit, _plans = prepare_foundation_engine(runtime_root, clock)
    bus = InMemoryEventBus()
    subscription = await engine.start(bus, "synthetic.input")
    event = EventEnvelope(
        event_type="synthetic.input",
        occurred_at=clock.now(),
        source="operator_cli",
        market_session=MarketSession.REGULAR,
        subject="AAPL",
        payload={
            "symbol": "AAPL",
            "timeframe": "1m",
            "run_id": "synthetic-demo",
            "values": {"price": price},
        },
    )
    try:
        await bus.publish("synthetic.input", event)
        await bus.join()
        return [
            {
                "strategy_id": evaluation.strategy_id,
                "strategy_version": evaluation.strategy_version,
                "mode": evaluation.mode.value,
                "outcome": evaluation.trace.outcome.value,
                "rule_versions": [
                    step.result.rule_version
                    for step in evaluation.trace.steps
                    if step.result is not None
                ],
                "context_hash": evaluation.context_hash,
                "strategy_definition_hash": evaluation.strategy_definition_hash,
                "compiled_plan_hash": evaluation.compiled_plan_hash,
                "registry_snapshot_hash": evaluation.registry_snapshot_hash,
                "audit_confirmed": evaluation.audit_confirmed,
                "eligible": evaluation.eligible,
            }
            for evaluation in audit.evaluations
        ]
    finally:
        await subscription.unsubscribe()
        await bus.close()
        audit.close()


def main() -> None:
    """Console-script compatible entry point."""
    app()
