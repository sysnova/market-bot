"""Top-level MarketBot operator CLI."""

import asyncio
import json
import selectors
from collections.abc import Coroutine
from pathlib import Path
from typing import Annotated, Any, Literal

import typer

from app import __version__
from app.common.clock import SystemClock
from app.contracts import EventEnvelope, MarketSession
from app.event_bus import InMemoryEventBus
from app.integration.foundation import prepare_foundation_engine

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
    pretty_exceptions_enable=False,
)


def _run_async[ResultT](coroutine: Coroutine[Any, Any, ResultT]) -> ResultT:
    """Run CLI coroutines on a loop supported by psycopg on Windows."""

    return asyncio.run(
        coroutine,
        loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
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
) -> None:
    """Operate a MarketBot deployment."""


def _placeholder(name: str, help_text: str) -> typer.Typer:
    group = typer.Typer(name=name, help=help_text, invoke_without_command=True)

    def unavailable() -> None:
        typer.echo(f"The {name} operator module is not installed.")

    group.callback()(unavailable)
    return group


for group_name, group_help in _GROUPS:
    app.add_typer(_placeholder(group_name, group_help), name=group_name)


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
        typer.Option(help="Ring the terminal bell for each local alert."),
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
    ] = Path(".runtime/status/long-term-v2.ready.json"),
) -> None:
    """Run the independent Long v2 process."""

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
    ] = Path(".runtime/status/swing-v2.ready.json"),
) -> None:
    """Run the independent Swing v2 process."""

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
    ] = Path(".runtime/status/intraday-v2.ready.json"),
) -> None:
    """Run the independent Intraday v2 process."""

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
        Path, typer.Option(help="Exact-version LONG portfolio YAML artifact.")
    ] = Path("configs/rules/long_portfolio/1.0.0.yaml"),
    runtime_root: Annotated[
        Path, typer.Option(help="Directory for the deduplicated LONG alert ledger.")
    ] = Path(".runtime"),
    ready_path: Annotated[
        Path, typer.Option(help="Readiness file written after subscribing to NATS.")
    ] = Path(".runtime/status/long-portfolio-v1.ready.json"),
) -> None:
    """Monitor solid, allocation-aware entries for the year-end LONG portfolio."""

    from app.integration.long_portfolio_composition import run_long_portfolio_process

    _run_async(
        run_long_portfolio_process(
            config_path=config_path,
            runtime_root=runtime_root,
            ready_path=ready_path,
        )
    )


@engine.command("patreon-caps")
def patreon_caps_process(
    config_path: Annotated[
        Path, typer.Option(help="Exact-version PatreonCaps YAML artifact.")
    ] = Path("configs/rules/patreon_caps/1.1.0.yaml"),
    ready_path: Annotated[
        Path, typer.Option(help="Readiness file written after NATS and PostgreSQL are ready.")
    ] = Path(".runtime/status/patreon-caps-v1.ready.json"),
) -> None:
    """Run the independent PatreonCaps v1 SHADOW process."""

    from app.integration.patreon_caps_composition import run_patreon_caps_process

    _run_async(run_patreon_caps_process(config_path=config_path, ready_path=ready_path))


@engine.command("elliott-wave")
def elliott_wave_process(
    once: Annotated[bool, typer.Option(help="Analyze held positions once and exit.")] = False,
    ready_path: Annotated[
        Path, typer.Option(help="Readiness file written after NATS and holdings are ready.")
    ] = Path(".runtime/status/elliott-wave-v0.ready.json"),
) -> None:
    """Run Elliott Wave shadow analysis only for positive local holdings."""

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
    """Run holdings-only cross-engine fusion in SHADOW mode."""

    from app.integration.signal_fusion_composition import run_signal_fusion_process

    summary = _run_async(run_signal_fusion_process(ready_path=ready_path, once=once))
    if summary is not None:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))


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
        typer.Option(help="Ring the terminal bell for each final human alert."),
    ] = True,
    ready_path: Annotated[
        Path,
        typer.Option(help="Readiness file written after all NATS subscriptions exist."),
    ] = Path(".runtime/status/alert-v2.ready.json"),
) -> None:
    """Run the Alert v2 NATS consumer process."""

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
        typer.Option(help="Ring the terminal bell for each confirmed buy."),
    ] = True,
    ready_path: Annotated[
        Path,
        typer.Option(help="Readiness file written after subscribing to NATS."),
    ] = Path(".runtime/status/confirmed-buy-monitor.ready.json"),
) -> None:
    """Show only confirmed buy events received through NATS."""

    from app.integration.confirmed_buy_monitor import run_confirmed_buy_monitor

    _run_async(run_confirmed_buy_monitor(ready_path=ready_path, bell=bell))


@alerts.command("long-portfolio")
def long_portfolio_monitor(
    bell: Annotated[bool, typer.Option(help="Ring for each new LONG portfolio alert.")] = True,
    history: Annotated[
        int, typer.Option(min=1, max=500, help="Persisted PostgreSQL alerts shown on startup.")
    ] = 25,
    ready_path: Annotated[
        Path, typer.Option(help="Readiness file written after history and NATS are ready.")
    ] = Path(".runtime/status/long-portfolio-monitor.ready.json"),
) -> None:
    """Show only persisted and live year-end LONG portfolio alerts."""

    from app.integration.long_portfolio_monitor import run_long_portfolio_monitor

    _run_async(run_long_portfolio_monitor(ready_path=ready_path, bell=bell, history=history))


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
    """Show Signal Fusion evidence or current SHADOW buy confirmations."""

    from app.integration.signal_fusion_monitor import run_signal_fusion_monitor

    _run_async(
        run_signal_fusion_monitor(
            mode=mode,
            ready_path=ready_path,
            bell=bell,
        )
    )


entry_watch = typer.Typer(
    name="entry-watch",
    help="Run the independent persistent entry-opportunity process.",
)
app.add_typer(entry_watch, name="entry-watch")


@entry_watch.command("serve")
def entry_watcher_process(
    ready_path: Annotated[
        Path,
        typer.Option(help="Readiness file written after PostgreSQL and NATS are ready."),
    ] = Path(".runtime/status/entry-watcher-v3.ready.json"),
) -> None:
    """Run the configured Entry Watcher PostgreSQL/NATS process (V3 by default)."""

    from app.integration.distributed_composition import run_entry_watcher_process

    _run_async(run_entry_watcher_process(ready_path=ready_path))


sec = typer.Typer(name="sec", help="Run the independent bounded SEC filing bot.")
app.add_typer(sec, name="sec")


@sec.command("daily")
def sec_daily(
    lookback_days: Annotated[
        int | None,
        typer.Option(
            min=1,
            max=30,
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
    """Execute PRIMARY v1 and SHADOW v2 once in the local in-process supervisor."""

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
