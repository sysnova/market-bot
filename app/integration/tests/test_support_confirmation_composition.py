from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.contracts import (
    MARKET_BAR_EVENT,
    SUPPORT_ASSESSMENT_EVENT,
    SUPPORT_TRANSITION_EVENT,
    BarTimeframe,
    EventEnvelope,
    MarketBar,
    SupportAssessment,
    SupportConfirmationType,
    SupportState,
    SupportTransition,
)
from app.integration.support_confirmation_composition import (
    SupportConfirmationRuntime,
    load_support_holdings,
)
from app.integration.support_confirmation_monitor import _format_assessment
from app.support_confirmation_engine import SupportContext


class _Universe:
    def __init__(self) -> None:
        self.holdings_calls = 0
        self.universe_calls = 0

    async def get_holdings(self) -> SimpleNamespace:
        self.holdings_calls += 1
        return SimpleNamespace(symbols=("TGT", "MSFT"), source="postgresql-local-holdings")

    async def get_universe(self) -> SimpleNamespace:
        self.universe_calls += 1
        return SimpleNamespace(symbols=("TGT", "MSFT", "WATCH_ONLY"))


async def test_support_universe_is_strictly_positive_holdings() -> None:
    provider = _Universe()

    snapshot = await load_support_holdings(provider)

    assert snapshot.symbols == ("TGT", "MSFT")
    assert provider.holdings_calls == 1
    assert provider.universe_calls == 0


async def test_support_universe_rejects_an_empty_portfolio() -> None:
    provider = _Universe()

    async def empty_holdings() -> SimpleNamespace:
        return SimpleNamespace(symbols=(), source="postgresql-local-holdings")

    provider.get_holdings = empty_holdings  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="positive local holding"):
        await load_support_holdings(provider)


def test_tmux_launcher_has_a_sibling_support_confirmation_window() -> None:
    launcher = Path("scripts/linux/start-market-bot.sh").read_text(encoding="utf-8")

    assert "-n SupportConfirmation" in launcher
    assert "--role support-confirmation" in launcher
    assert "SUPPORT CONFIRMATION" in launcher


def test_panel_separates_reaction_from_reversal() -> None:
    item = SimpleNamespace(
        occurred_at=datetime(2026, 8, 2, 20, tzinfo=UTC),
        symbol="TGT",
        state=SupportState.RECLAIMED,
        confirmation_type=SupportConfirmationType.SWEEP_RECLAIM,
        reaction_score=Decimal("82"),
        reversal_score=Decimal("25"),
        support_score=Decimal("80"),
        current_price=Decimal("105"),
        zone_low=Decimal("99"),
        zone_high=Decimal("101"),
        invalidation=Decimal("96"),
        b_wave_risk=True,
    )

    text = _format_assessment(item)

    assert "RECLAIMED" in text
    assert "REACT 82" in text
    assert "REV 25" in text
    assert "B-RISK YES" in text


class _Publisher:
    def __init__(self) -> None:
        self.items: list[tuple[str, EventEnvelope]] = []

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        self.items.append((subject, envelope))


class _Engine:
    def __init__(self) -> None:
        self.state = SupportState.RECLAIMED
        self.hash = f"sha256:{'1' * 64}"
        self.contexts: list[SupportContext] = []

    def evaluate(self, context: SupportContext) -> SupportAssessment:
        self.contexts.append(context)
        return SupportAssessment(
            symbol=context.symbol,
            occurred_at=context.daily_bars[-1].timestamp,
            engine_version="0.1.0",
            state=self.state,
            confirmation_type=SupportConfirmationType.SWEEP_RECLAIM,
            current_price=context.daily_bars[-1].close,
            zone_low=Decimal("99"),
            zone_center=Decimal("100"),
            zone_high=Decimal("101"),
            invalidation=Decimal("96"),
            support_score=Decimal("80"),
            reaction_score=Decimal("82"),
            reversal_score=Decimal("25"),
            confidence=Decimal("0.82"),
            reasons=("test",),
            context_hash=self.hash,
        )


def _bar(
    index: int,
    *,
    symbol: str = "TGT",
    timeframe: BarTimeframe = BarTimeframe.DAY_1,
    final: bool = True,
) -> MarketBar:
    return MarketBar(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=datetime(2026, 7, 1, tzinfo=UTC) + timedelta(days=index),
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
        volume=Decimal("1000"),
        is_final=final,
        source="test",
        feed="test",
    )


def _envelope(bar: MarketBar, *, event_type: str = MARKET_BAR_EVENT) -> EventEnvelope:
    return EventEnvelope(
        event_type=event_type,
        occurred_at=bar.timestamp,
        source="test",
        subject=bar.symbol,
        payload=bar,
    )


async def test_runtime_publishes_assessment_transition_and_deduplicates() -> None:
    publisher = _Publisher()
    engine = _Engine()
    runtime = SupportConfirmationRuntime(engine=engine, publisher=publisher)

    published = await runtime.bootstrap(
        (*(_bar(index) for index in range(15)), _bar(1, symbol="WATCH")),
        symbols=("TGT",),
    )

    assert published == 1
    assert [item.event_type for _, item in publisher.items] == [
        SUPPORT_ASSESSMENT_EVENT,
        SUPPORT_TRANSITION_EVENT,
    ]
    assert publisher.items[0][0].endswith("assessment.TGT")
    assert publisher.items[1][0].endswith("transition.RECLAIMED.TGT")

    await runtime.handle_market(_envelope(_bar(15)))
    assert len(publisher.items) == 2

    engine.hash = f"sha256:{'2' * 64}"
    await runtime.handle_market(_envelope(_bar(16)))
    assert len(publisher.items) == 3

    engine.state = SupportState.STRUCTURE_CONFIRMED
    engine.hash = f"sha256:{'3' * 64}"
    await runtime.handle_market(_envelope(_bar(17)))
    assert len(publisher.items) == 5
    transition = publisher.items[-1][1].payload
    assert isinstance(transition, SupportTransition)
    assert engine.contexts[-1].previous_assessment is not None


async def test_runtime_restores_state_and_ignores_irrelevant_market_events() -> None:
    publisher = _Publisher()
    engine = _Engine()
    runtime = SupportConfirmationRuntime(engine=engine, publisher=publisher)
    assessment = engine.evaluate(
        SupportContext(symbol="TGT", daily_bars=tuple(_bar(index) for index in range(15)))
    )
    restore = EventEnvelope(
        event_type=SUPPORT_ASSESSMENT_EVENT,
        occurred_at=assessment.occurred_at,
        source="support-confirmation-v0",
        subject="TGT",
        payload=assessment.model_dump(mode="json"),
    )
    await runtime.restore_assessment(restore)
    await runtime.restore_assessment(
        EventEnvelope(
            event_type="ignored",
            occurred_at=assessment.occurred_at,
            source="test",
            subject="TGT",
            payload={},
        )
    )
    assert await runtime.bootstrap(
        tuple(_bar(index) for index in range(15)), symbols=("TGT",)
    ) == 0

    await runtime.handle_market(_envelope(_bar(15), event_type="ignored"))
    await runtime.handle_market(_envelope(_bar(15, symbol="MSFT")))
    await runtime.handle_market(_envelope(_bar(15, final=False)))
    await runtime.handle_market(
        _envelope(_bar(15, timeframe=BarTimeframe.MINUTE_15))
    )
    await runtime.handle_market(_envelope(_bar(15, timeframe=BarTimeframe.WEEK_1)))
    assert publisher.items == []
