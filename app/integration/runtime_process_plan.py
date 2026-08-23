"""Canonical distributed runtime process topology.

Launchers own platform presentation and process supervision. This module owns which
processes exist, their command arguments, readiness files, and startup dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .marketbot_definition import EngineMode, EngineSlot, MarketBotDefinition


@dataclass(frozen=True, slots=True)
class RuntimeProcessSpec:
    """One independently supervised runtime process."""

    name: str
    arguments: tuple[str, ...]
    engine_slot: EngineSlot | None = None
    ready_path: Path | None = None
    dependencies: tuple[str, ...] = ()
    operator_monitor: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "arguments": list(self.arguments),
            "engine_slot": self.engine_slot.value if self.engine_slot is not None else None,
            "ready_path": str(self.ready_path) if self.ready_path is not None else None,
            "dependencies": list(self.dependencies),
            "operator_monitor": self.operator_monitor,
        }


@dataclass(frozen=True, slots=True)
class RuntimeProcessPlan:
    """Validated process graph derived from one MarketBot assembly."""

    definition_id: str
    definition_version: str
    active_engine_slots: tuple[EngineSlot, ...]
    processes: tuple[RuntimeProcessSpec, ...]

    @property
    def headless_processes(self) -> tuple[RuntimeProcessSpec, ...]:
        return tuple(process for process in self.processes if not process.operator_monitor)

    @property
    def operator_monitors(self) -> tuple[RuntimeProcessSpec, ...]:
        return tuple(process for process in self.processes if process.operator_monitor)

    def process(self, name: str) -> RuntimeProcessSpec:
        for process in self.processes:
            if process.name == name:
                return process
        raise KeyError(name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "definition_id": self.definition_id,
            "definition_version": self.definition_version,
            "active_engine_slots": [slot.value for slot in self.active_engine_slots],
            "startup_batches": [list(batch) for batch in startup_batches(self.headless_processes)],
            "processes": [process.to_dict() for process in self.processes],
        }


def build_runtime_process_plan(
    definition: MarketBotDefinition,
    *,
    runtime_root: Path,
    symbols: str | None = None,
    bell: bool = True,
) -> RuntimeProcessPlan:
    """Build the distributed runtime graph from the selected engine modes."""

    status_root = runtime_root / "status"
    active_slots = tuple(
        slot for slot, spec in definition.engines.items() if spec.mode is EngineMode.ACTIVE
    )
    active = set(active_slots)
    processes: list[RuntimeProcessSpec] = []

    def ready(name: str) -> Path:
        return status_root / f"{name}.ready.json"

    def add(
        name: str,
        arguments: tuple[str, ...],
        *,
        slot: EngineSlot | None = None,
        dependencies: tuple[str, ...] = (),
        has_readiness: bool = True,
        operator_monitor: bool = False,
    ) -> None:
        if slot is not None and slot not in active:
            return
        processes.append(
            RuntimeProcessSpec(
                name=name,
                arguments=arguments,
                engine_slot=slot,
                ready_path=ready(name) if has_readiness else None,
                dependencies=dependencies,
                operator_monitor=operator_monitor,
            )
        )

    add(
        "outbox-relay",
        (
            "run",
            "marketbot",
            "outbox",
            "serve",
            "--ready-path",
            str(ready("outbox-relay")),
        ),
    )
    initial_specs = (
        (
            "alert",
            EngineSlot.ALERT,
            (
                "run",
                "marketbot",
                "alerts",
                "serve",
                "--runtime-root",
                str(runtime_root),
                "--ready-path",
                str(ready("alert")),
            )
            + (() if bell else ("--no-bell",)),
        ),
        (
            "entry-watcher",
            EngineSlot.ENTRY_WATCHER,
            (
                "run",
                "marketbot",
                "entry-watch",
                "serve",
                "--ready-path",
                str(ready("entry-watcher")),
            ),
        ),
        (
            "entry-opportunity",
            EngineSlot.ENTRY_OPPORTUNITY,
            (
                "run",
                "marketbot",
                "entry-opportunity",
                "serve",
                "--ready-path",
                str(ready("entry-opportunity")),
            ),
        ),
        (
            "entry-recovery",
            EngineSlot.ENTRY_RECOVERY,
            (
                "run",
                "marketbot",
                "engine",
                "entry-recovery",
                "--ready-path",
                str(ready("entry-recovery")),
            ),
        ),
    )
    for name, slot, arguments in initial_specs:
        dependencies = ("outbox-relay",)
        if slot is EngineSlot.ENTRY_OPPORTUNITY:
            dependencies += ("market-history-v1",)
        add(name, arguments, slot=slot, dependencies=dependencies)

    add(
        "market-history-v1",
        (
            "run",
            "marketbot",
            "market",
            "history",
            "--ready-path",
            str(ready("market-history-v1")),
        ),
        dependencies=("outbox-relay",),
    )
    add(
        "long-portfolio-v1",
        (
            "run",
            "marketbot",
            "engine",
            "long-portfolio",
            "--runtime-root",
            str(runtime_root),
            "--ready-path",
            str(ready("long-portfolio-v1")),
        ),
        slot=EngineSlot.LONG_PORTFOLIO,
        dependencies=("market-history-v1",),
    )

    analytical_dependencies = ("market-history-v1",)
    if EngineSlot.LONG_PORTFOLIO in active:
        analytical_dependencies += ("long-portfolio-v1",)
    symbol_arguments = ("--symbols", symbols) if symbols else ()
    analytical_specs = (
        (
            "long-term",
            EngineSlot.LONG_TERM,
            ("run", "marketbot", "engine", "long"),
        ),
        ("swing", EngineSlot.SWING, ("run", "marketbot", "engine", "swing")),
        (
            "swing-channel-4h",
            EngineSlot.SWING_CHANNEL_4H,
            ("run", "marketbot", "engine", "swing-channel-4h"),
        ),
        (
            "4hgeri",
            EngineSlot.GERI_4H,
            ("run", "marketbot", "engine", "4hgeri"),
        ),
        (
            "swing-trade",
            EngineSlot.SWING_TRADE,
            ("run", "marketbot", "engine", "swing-trade"),
        ),
        (
            "intraday",
            EngineSlot.INTRADAY,
            ("run", "marketbot", "engine", "intraday"),
        ),
        (
            "market-rotation-v1",
            EngineSlot.MARKET_ROTATION,
            ("run", "marketbot", "engine", "rotation"),
        ),
        (
            "portfolio-flow-v1",
            EngineSlot.PORTFOLIO_FLOW,
            ("run", "marketbot", "engine", "portfolio-flow"),
        ),
        (
            "patreon-caps-v1",
            EngineSlot.PATREON_CAPS,
            ("run", "marketbot", "engine", "patreon-caps"),
        ),
        (
            "elliott-wave-v0",
            EngineSlot.ELLIOTT_WAVE,
            ("run", "marketbot", "engine", "elliott-wave"),
        ),
        (
            "support-confirmation-v0",
            EngineSlot.SUPPORT_CONFIRMATION,
            ("run", "marketbot", "engine", "support-confirmation"),
        ),
        (
            "volume-structure-v1",
            EngineSlot.VOLUME_STRUCTURE,
            ("run", "marketbot", "engine", "volume-structure"),
        ),
        (
            "options-gamma-v1",
            EngineSlot.OPTIONS_GAMMA,
            ("run", "marketbot", "engine", "options-gamma"),
        ),
        (
            "signal-fusion-v0",
            EngineSlot.SIGNAL_FUSION,
            ("run", "marketbot", "engine", "signal-fusion"),
        ),
        (
            "news-intelligence-v1",
            EngineSlot.NEWS_INTELLIGENCE,
            ("run", "marketbot", "engine", "news-intelligence"),
        ),
    )
    for name, slot, command in analytical_specs:
        arguments = (*command, "--ready-path", str(ready(name)))
        if slot in {
            EngineSlot.LONG_TERM,
            EngineSlot.SWING,
            EngineSlot.SWING_CHANNEL_4H,
            EngineSlot.GERI_4H,
            EngineSlot.SWING_TRADE,
            EngineSlot.INTRADAY,
            EngineSlot.VOLUME_STRUCTURE,
            EngineSlot.OPTIONS_GAMMA,
        }:
            arguments += symbol_arguments
        if slot is EngineSlot.NEWS_INTELLIGENCE:
            dependencies = ("alert", "entry-watcher", "entry-opportunity")
        elif slot is EngineSlot.SUPPORT_CONFIRMATION:
            dependencies = ("market-history-v1",)
        elif (
            slot is EngineSlot.GERI_4H
            and slot in definition.engines
            and definition.engines[slot].implementation in {"1.2.0", "1.3.0", "1.4.0", "1.5.0"}
        ):
            dependencies = ("market-history-v1",) + (
                ("support-confirmation-v0",) if EngineSlot.SUPPORT_CONFIRMATION in active else ()
            )
        elif slot is EngineSlot.SWING_TRADE:
            dependencies = (
                "market-history-v1",
                "4hgeri",
                "entry-opportunity",
            ) + (("support-confirmation-v0",) if EngineSlot.SUPPORT_CONFIRMATION in active else ())
        elif slot is EngineSlot.SWING:
            dependencies = analytical_dependencies + (
                ("support-confirmation-v0",) if EngineSlot.SUPPORT_CONFIRMATION in active else ()
            )
        else:
            dependencies = analytical_dependencies
        add(name, arguments, slot=slot, dependencies=dependencies)

    add(
        "confirmed-buy-monitor",
        (
            "run",
            "marketbot",
            "alerts",
            "confirmed",
            "--ready-path",
            str(ready("confirmed-buy-monitor")),
        )
        + (() if bell else ("--no-bell",)),
        slot=EngineSlot.ALERT,
        dependencies=("alert",),
        operator_monitor=True,
    )

    # Gamma is fail-open context: an options-provider outage must not block equities.
    stream_dependencies = tuple(
        process.name
        for process in processes
        if not process.operator_monitor and process.engine_slot is not EngineSlot.OPTIONS_GAMMA
    )
    add(
        "alpaca-market-stream",
        ("run", "marketbot", "market", "stream", *symbol_arguments),
        dependencies=stream_dependencies,
        has_readiness=False,
    )

    plan = RuntimeProcessPlan(
        definition_id=definition.definition_id,
        definition_version=definition.version,
        active_engine_slots=active_slots,
        processes=tuple(processes),
    )
    startup_batches(plan.headless_processes)
    startup_batches(plan.processes)
    return plan


def startup_batches(
    processes: tuple[RuntimeProcessSpec, ...],
) -> tuple[tuple[str, ...], ...]:
    """Return deterministic parallel startup batches for a process graph."""

    by_name = {process.name: process for process in processes}
    if len(by_name) != len(processes):
        raise ValueError("runtime process names must be unique")
    for process in processes:
        missing = set(process.dependencies) - set(by_name)
        if missing:
            raise ValueError(f"missing process dependency for {process.name}: {sorted(missing)}")

    remaining = dict(by_name)
    completed: set[str] = set()
    batches: list[tuple[str, ...]] = []
    while remaining:
        batch = tuple(
            name for name, process in remaining.items() if set(process.dependencies) <= completed
        )
        if not batch:
            raise ValueError(f"runtime process dependency cycle: {sorted(remaining)}")
        batches.append(batch)
        completed.update(batch)
        for name in batch:
            del remaining[name]
    return tuple(batches)
