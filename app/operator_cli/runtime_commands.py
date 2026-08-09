"""Definition and distributed runtime-plan operator commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

import typer

from app.common.settings import AppSettings
from app.integration.marketbot_definition import (
    EngineMode,
    load_configured_marketbot_definition,
)
from app.integration.runtime_process_plan import build_runtime_process_plan


def register_runtime_commands(app: typer.Typer) -> None:
    """Register lightweight assembly and topology diagnostics on the root CLI."""

    app.command("assembly")(show_assembly)
    app.command("runtime-slots")(show_runtime_slots)
    app.command("runtime-plan")(show_runtime_plan)


def show_assembly() -> None:
    """Show the implementation, strategy, and mode selected for every engine."""

    from app.integration.engine_assembly import MarketBotAssembly

    assembly = MarketBotAssembly.from_settings(AppSettings())
    typer.echo(
        json.dumps(
            {
                "definition_id": assembly.definition.definition_id,
                "version": assembly.definition.version,
                "source": str(assembly.definition.source),
                "engines": {
                    slot.value: {
                        "implementation": spec.implementation,
                        "strategy": {
                            "kind": spec.strategy.kind.value,
                            "version": spec.strategy.version,
                            "artifact": (
                                str(spec.strategy.artifact)
                                if spec.strategy.artifact is not None
                                else None
                            ),
                        },
                        "mode": spec.mode.value,
                    }
                    for slot, spec in assembly.definition.engines.items()
                },
            },
            indent=2,
            sort_keys=True,
        )
    )


def show_runtime_slots(
    mode: Annotated[
        Literal["active", "scheduled", "on-demand"],
        typer.Option(help="Operational lifecycle mode selected from the MarketBot definition."),
    ] = "active",
) -> None:
    """Print engine slots selected for one operational lifecycle mode."""

    definition = load_configured_marketbot_definition(AppSettings())
    for slot, spec in definition.engines.items():
        if spec.mode is not EngineMode(mode):
            continue
        typer.echo(slot.value)


def show_runtime_plan(
    runtime_root: Annotated[
        Path,
        typer.Option(help="Directory used for readiness and runtime state."),
    ] = Path(".runtime"),
    symbols: Annotated[
        str | None,
        typer.Option(help="Optional comma-separated temporary universe."),
    ] = None,
    bell: Annotated[
        bool,
        typer.Option("--bell/--no-bell", help="Enable audible operator notifications."),
    ] = True,
) -> None:
    """Print the canonical distributed process and dependency plan as JSON."""

    definition = load_configured_marketbot_definition(AppSettings())
    plan = build_runtime_process_plan(
        definition,
        runtime_root=runtime_root,
        symbols=symbols,
        bell=bell,
    )
    typer.echo(json.dumps(plan.to_dict(), indent=2, sort_keys=True))
