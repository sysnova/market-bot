"""Private one-shot engine assembly. No live services, NATS, or shared decision state."""

from __future__ import annotations

import asyncio
import json
import selectors
import sys
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

from app.common.clock import SystemClock
from app.common.market_session import is_regular_analytical_bar
from app.common.settings import AppSettings
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

from .engine_assembly import MarketBotAssembly
from .market_history_composition import (
    MarketHistoryLoader,
    _build_rest,  # pyright: ignore[reportPrivateUsage]
)
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

    async def support(ticker: str) -> dict[str, object]:
        from .support_confirmation_composition import (
            SUPPORT_HISTORY_REQUESTS,
            SupportConfirmationRuntime,
        )

        events = ManualEvents()
        runtime = SupportConfirmationRuntime(
            engine=assembly.build_support_confirmation(),
            publisher=events,
            clock=clock,
        )
        await runtime.bootstrap(
            await history("manual-support", (ticker,), SUPPORT_HISTORY_REQUESTS),
            symbols=(ticker,),
        )
        return collect(events)

    async def geri(ticker: str) -> dict[str, object]:
        from .swing_4h_geri_composition import GERI_HISTORY_REQUESTS, Swing4HGeriRuntime

        events = ManualEvents()
        runtime = Swing4HGeriRuntime(
            engine=assembly.build_4hgeri(),
            publisher=events,
            clock=clock,
            emit_countertrend_signals=False,
        )
        # Only this invocation's support assessment may inform the 4H context.
        for event in tuple(sources):
            await runtime.restore_support(event)
        await runtime.bootstrap(
            await history("manual-4hgeri", (ticker,), GERI_HISTORY_REQUESTS),
            symbols=(ticker,),
        )
        for event in tuple(sources):
            await runtime.handle_analysis(event)
        if not events.events:
            raise AnalysisSkipped("no_4hgeri_assessment_from_completed_regular_bars")
        return collect(events)

    report = await SymbolAnalysisOrchestrator(
        core=AnalysisStep("core", core),
        parallel=(AnalysisStep("support-confirmation", support),),
        dependent=AnalysisStep("4hgeri", geri),
        clock=clock,
    ).analyze(symbol, timeout_seconds=timeout_seconds)
    report["transport"] = "DIRECT_ISOLATED"
    report["universe_modified"] = False
    return report


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
