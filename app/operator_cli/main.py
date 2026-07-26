"""Top-level MarketBot operator CLI."""

import asyncio
import json
from pathlib import Path
from typing import Annotated

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
            help="Comma-separated temporary universe; overrides Supabase for this run."
        ),
    ] = None,
) -> None:
    """Run the realtime analysis-only bot; this command cannot submit orders."""

    from app.integration.live_composition import run_live_analysis

    summary = asyncio.run(
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

    summary = asyncio.run(_run_demo(price=price, runtime_root=runtime_root))
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
