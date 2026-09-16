"""Private one-shot engine assembly. No live services, NATS, or shared decision state."""

from __future__ import annotations

import asyncio
import json
import selectors
import sys
from contextlib import redirect_stdout
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

from app.common.clock import SystemClock
from app.common.market_session import is_regular_analytical_bar
from app.common.settings import AppSettings, Environment
from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    AnalysisResult,
    BarTimeframe,
    EventEnvelope,
    MarketBar,
    MarketHistoryRequirement,
)
from app.market_history_engine import MarketHistoryService
from app.market_history_engine.service import BarCoverage
from app.patreon_caps_engine import PatreonCapsEvaluation, PatreonCapsPolicy
from app.persistence import create_database_engine

from .engine_assembly import EngineSlot, MarketBotAssembly
from .market_history_composition import (
    MarketHistoryLoader,
    _build_rest,  # pyright: ignore[reportPrivateUsage]
)
from .postgres_universe import PostgresUniverseClient
from .symbol_analysis_composition import AnalysisSkipped, AnalysisStep, SymbolAnalysisOrchestrator


class ManualEvents:
    """Results owned by this invocation; never forwarded to a broker."""

    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        if envelope.event_type == "universe.changed":
            raise RuntimeError("manual analysis cannot change a universe")
        self.events.append(envelope)

    def result(self) -> dict[str, object]:
        return {"events": [event.model_dump(mode="json") for event in self.events]}


class ManualHistory:
    """MarketHistoryService repository scoped to a single request, in memory."""

    def __init__(self) -> None:
        self.bars: dict[tuple[str, BarTimeframe, datetime], MarketBar] = {}

    async def coverage(
        self,
        symbols: tuple[str, ...],
        timeframe: BarTimeframe,
    ) -> dict[str, BarCoverage]:
        return {symbol: BarCoverage(0, None) for symbol in symbols}

    async def upsert(self, bars: tuple[MarketBar, ...]) -> int:
        for bar in bars:
            self.bars[bar.symbol, bar.timeframe, bar.timestamp] = bar
        return len(bars)

    async def load_latest(
        self,
        symbols: tuple[str, ...],
        timeframe: BarTimeframe,
        *,
        limit_per_symbol: int,
        regular_session_only: bool = False,
    ) -> tuple[MarketBar, ...]:
        result: list[MarketBar] = []
        for symbol in symbols:
            values = sorted(
                (
                    bar
                    for bar in self.bars.values()
                    if bar.symbol == symbol
                    and bar.timeframe == timeframe
                    and (not regular_session_only or is_regular_analytical_bar(bar))
                ),
                key=lambda bar: bar.timestamp,
            )
            result.extend(values[-limit_per_symbol:])
        return tuple(result)


class ManualPatreonStore:
    async def save(self, evaluation: PatreonCapsEvaluation) -> bool:
        return True

    async def latest_transition_times(self, *, rule_version: str) -> dict[str, datetime]:
        return {}


async def analyze_in_process(
    symbol: str,
    *,
    timeout_seconds: float,
    runtime_root: Path,
) -> dict[str, object]:
    """Called only in a fresh child process, without shared cache initialization."""
    from .live_composition import run_live_analysis

    settings = AppSettings()
    assembly = MarketBotAssembly.from_settings(settings)
    clock = SystemClock()
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    portfolio = PostgresUniverseClient(database)
    sources: list[EventEnvelope] = []

    async def history(
        name: str,
        symbols: tuple[str, ...],
        requirements: tuple[MarketHistoryRequirement, ...],
    ) -> tuple[MarketBar, ...]:
        repository = ManualHistory()
        rest = _build_rest(settings)
        service = MarketHistoryService(
            rest=rest,
            repository=repository,
            feed=settings.alpaca_data_feed,
            batch_size=settings.alpaca_rest_batch_size,
        )
        try:
            return await MarketHistoryLoader(client=service, repository=repository).ensure_and_load(
                engine_id=name,
                symbols=symbols,
                requirements=requirements,
                as_of=clock.now(),
            )
        finally:
            await rest.close()

    def collect(events: ManualEvents) -> dict[str, object]:
        sources.extend(events.events)
        if not events.events:
            raise AnalysisSkipped("no_assessment_from_current_inputs")
        return events.result()

    async def core(ticker: str) -> dict[str, object]:
        result = await run_live_analysis(
            once=True,
            runtime_root=runtime_root,
            bell=False,
            mirror_to_nats=False,
            symbols=(ticker,),
            include_analyses=True,
            isolated=True,
        )
        if result is None:
            raise RuntimeError("core returned no result")
        for raw in result["analyses"]:
            analysis = AnalysisResult.model_validate(raw, strict=False)
            sources.append(
                EventEnvelope(
                    event_type=ANALYSIS_RESULT_EVENT,
                    occurred_at=analysis.as_of,
                    source=analysis.engine_id,
                    subject=ticker,
                    payload=analysis,
                )
            )
        return result

    async def rotation(ticker: str) -> dict[str, object]:
        from app.common.market_session import is_completed_daily_bar
        from app.market_rotation_engine import Bar

        from .market_rotation_composition import ROTATION_HISTORY_REQUESTS
        from .market_rotation_store import PostgresMarketRotationStore

        profiles = await PostgresMarketRotationStore(database).load_profiles()
        symbols = tuple(
            dict.fromkeys(s for p in profiles for s in (*p.symbols, p.proxy, p.benchmark))
        )
        bars = await history("manual-rotation", symbols, ROTATION_HISTORY_REQUESTS)
        now = clock.now()
        series = {
            s: tuple(
                Bar(b.close, b.volume)
                for b in bars
                if b.symbol == s and is_completed_daily_bar(b, as_of=now)
            )
            for s in symbols
        }
        return {
            "scope": "global-market",
            "requested_symbol": ticker,
            "sectors": assembly.build_market_rotation().analyze(profiles, series),
            "watchlist_additions": [],
        }

    async def long_portfolio(ticker: str) -> dict[str, object]:
        allocations = await portfolio.get_portfolio_allocations()
        if not any(item.symbol == ticker for item in allocations):
            raise AnalysisSkipped("PORT_YTD_allocation_required")
        engine = assembly.build_long_portfolio(allocations=allocations)
        quantity = await portfolio.get_holding_quantity(ticker)
        alerts = []
        for event in sources:
            if isinstance(event.payload, AnalysisResult):
                alert = engine.ingest(event.payload, now=clock.now(), held_quantity=quantity)
                if alert is not None:
                    alerts.append(alert.model_dump(mode="json"))
        state = engine.state_for(ticker, updated_at=clock.now())
        return {"alerts": alerts, "state": state.model_dump(mode="json") if state else None}

    async def patreon(ticker: str) -> dict[str, object]:
        from .patreon_caps_composition import PATREON_HISTORY_REQUESTS, PatreonCapsRuntime

        policy = cast("PatreonCapsPolicy", assembly.resolve_strategy(EngineSlot.PATREON_CAPS))
        allocations = await portfolio.get_portfolio_allocations()
        selected = tuple(dict.fromkeys((ticker, *policy.macro_symbols)))
        bars = await history("manual-patreon", selected, PATREON_HISTORY_REQUESTS)
        events = ManualEvents()
        runtime = PatreonCapsRuntime(
            engine=assembly.build_patreon_caps(),
            publisher=events,
            store=ManualPatreonStore(),
            portfolio_data=portfolio,
            allocations={item.symbol: item.weight_percent for item in allocations},
            portfolio_capital_usd=policy.portfolio_capital_usd,
            macro_symbols=policy.macro_symbols,
            require_hourly=policy.lesson_enabled,
        )
        await runtime.bootstrap(bars, symbols=selected)
        for event in tuple(sources):
            await runtime.handle_analysis(event)
        await runtime.complete_hydration()
        return collect(events)

    async def elliott(ticker: str) -> dict[str, object]:
        from .elliott_wave_composition import ELLIOTT_HISTORY_REQUESTS, ElliottWaveRuntime

        if await portfolio.get_holding_quantity(ticker) <= Decimal():
            raise AnalysisSkipped("positive_holding_required")
        events = ManualEvents()
        runtime = ElliottWaveRuntime(engine=assembly.build_elliott_wave(), publisher=events)
        await runtime.bootstrap(
            await history("manual-elliott", (ticker,), ELLIOTT_HISTORY_REQUESTS),
            symbols=(ticker,),
        )
        return collect(events)

    async def support(ticker: str) -> dict[str, object]:
        from .support_confirmation_composition import (
            SUPPORT_HISTORY_REQUESTS,
            SupportConfirmationRuntime,
        )

        events = ManualEvents()
        runtime = SupportConfirmationRuntime(
            engine=assembly.build_support_confirmation(),
            publisher=events,
        )
        await runtime.bootstrap(
            await history("manual-support", (ticker,), SUPPORT_HISTORY_REQUESTS),
            symbols=(ticker,),
        )
        return collect(events)

    async def portfolio_flow(ticker: str) -> dict[str, object]:
        raise AnalysisSkipped("requires_live_quote_trade_window")

    async def fusion(ticker: str) -> dict[str, object]:
        from .signal_fusion_composition import SignalFusionRuntime

        quantity = await portfolio.get_holding_quantity(ticker)
        if quantity <= Decimal():
            raise AnalysisSkipped("positive_holding_required")
        events = ManualEvents()
        runtime = SignalFusionRuntime(
            engine=assembly.build_signal_fusion(),
            publisher=events,
            symbols=(ticker,),
            holding_quantities={ticker: quantity},
        )
        for event in sources:
            await runtime.handle_source(event)
        await runtime.complete_hydration()
        return collect(events)

    try:
        report = await SymbolAnalysisOrchestrator(
            core=AnalysisStep("core", core),
            parallel=(
                AnalysisStep("market-rotation", rotation),
                AnalysisStep("long-portfolio", long_portfolio),
                AnalysisStep("patreon-caps", patreon),
                AnalysisStep("elliott-wave", elliott),
                AnalysisStep("support-confirmation", support),
                AnalysisStep("portfolio-flow", portfolio_flow),
            ),
            fusion=AnalysisStep("signal-fusion", fusion),
        ).analyze(symbol, timeout_seconds=timeout_seconds)
        report["transport"] = "DIRECT_ISOLATED"
        report["universe_modified"] = False
        return report
    finally:
        await database.dispose()


def main() -> None:
    # Reserve stdout for the single structured response; diagnostics go to stderr.
    with redirect_stdout(sys.stderr):
        report = asyncio.run(
            analyze_in_process(
                sys.argv[1],
                timeout_seconds=float(sys.argv[2]),
                runtime_root=Path(sys.argv[3]),
            ),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    print(json.dumps(report, default=str))


if __name__ == "__main__":
    main()
