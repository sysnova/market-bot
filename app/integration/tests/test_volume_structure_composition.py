from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    BarTimeframe,
    EventEnvelope,
    MarketBar,
    NamedValue,
    PatternDirection,
)
from app.integration.volume_structure_composition import VolumeStructureRuntime
from app.volume_structure_engine import VolumeStructureContext


class _Publisher:
    def __init__(self) -> None:
        self.items: list[tuple[str, EventEnvelope]] = []

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        self.items.append((subject, envelope))


class _Engine:
    def __init__(self) -> None:
        self.hash = "sha256:" + "1" * 64
        self.contexts: list[VolumeStructureContext] = []

    def evaluate(self, context: VolumeStructureContext) -> AnalysisResult:
        self.contexts.append(context)
        return AnalysisResult(
            engine_id="volume-structure",
            engine_version="1.0.0",
            symbol=context.symbol,
            horizon=AnalysisHorizon.VOLUME_STRUCTURE,
            as_of=context.weekly_bars[-1].timestamp,
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            score=Decimal("80"),
            confidence=Decimal("0.8"),
            reasons=("weekly_obv_bullish_divergence",),
            metrics=(NamedValue(name="evidence_boost", value=Decimal("6")),),
            context_hash=self.hash,
        )


async def test_runtime_publishes_once_and_preserves_previous_result() -> None:
    publisher = _Publisher()
    engine = _Engine()
    runtime = VolumeStructureRuntime(engine=engine, publisher=publisher)

    assert await runtime.bootstrap(_bars(12), symbols=("VLO",)) == 1
    assert publisher.items[0][0] == "marketbot.v1.analysis.result.VOLUME_STRUCTURE.VLO"
    assert publisher.items[0][1].event_type == ANALYSIS_RESULT_EVENT
    assert await runtime.bootstrap(_bars(12), symbols=("VLO",)) == 0

    engine.hash = "sha256:" + "2" * 64
    await runtime.handle_market(_envelope(_bar(12)))

    assert len(publisher.items) == 2
    assert engine.contexts[-1].previous_result is not None


async def test_runtime_ignores_nonweekly_nonfinal_and_unknown_symbols() -> None:
    publisher = _Publisher()
    runtime = VolumeStructureRuntime(engine=_Engine(), publisher=publisher)
    await runtime.bootstrap(_bars(12), symbols=("VLO",))
    publisher.items.clear()

    await runtime.handle_market(_envelope(_bar(12, final=False)))
    await runtime.handle_market(
        _envelope(_bar(12, timeframe=BarTimeframe.DAY_1))
    )
    await runtime.handle_market(_envelope(_bar(12, symbol="XOM")))

    assert publisher.items == []


def _bars(count: int) -> tuple[MarketBar, ...]:
    return tuple(_bar(index) for index in range(count))


def _bar(
    index: int,
    *,
    symbol: str = "VLO",
    timeframe: BarTimeframe = BarTimeframe.WEEK_1,
    final: bool = True,
) -> MarketBar:
    return MarketBar(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=datetime(2026, 5, 1, 20, tzinfo=UTC) + timedelta(weeks=index),
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("98"),
        close=Decimal("101"),
        volume=Decimal("1000"),
        is_final=final,
        source="test",
        feed="test",
    )


def _envelope(bar: MarketBar) -> EventEnvelope:
    return EventEnvelope(
        event_type="market.bar.received",
        occurred_at=bar.timestamp,
        source="test",
        subject=bar.symbol,
        payload=bar,
    )
