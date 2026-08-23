from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.contracts.intraday_opportunity import (
    IntradayCloseReason,
    IntradayOpportunityStatus,
    IntradaySide,
    IntradayTradeAction,
)
from app.intraday_opportunity_engine import (
    ActiveIntradayOpportunityError,
    InMemoryIntradayOpportunityStore,
    IntradayOpportunityEngine,
)

NOW = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)
def _source(suffix: int) -> UUID:
    return UUID(f"0195f3a5-9000-7000-8000-{suffix:012d}")


@pytest.fixture
def store() -> InMemoryIntradayOpportunityStore:
    return InMemoryIntradayOpportunityStore()


@pytest.fixture
def engine(store: InMemoryIntradayOpportunityStore) -> IntradayOpportunityEngine:
    counter = iter(range(100, 200))

    def id_factory() -> UUID:
        return UUID(f"0195f3a5-9000-7000-8000-{next(counter):012d}")

    return IntradayOpportunityEngine(store=store, id_factory=id_factory)


@pytest.mark.unit
async def test_long_round_trip_uses_ask_to_enter_and_bid_to_exit(
    engine: IntradayOpportunityEngine,
    store: InMemoryIntradayOpportunityStore,
) -> None:
    opened = await engine.open_position(
        source_event_id=_source(1),
        symbol="aapl",
        strategy_id="support-reversal-v1",
        side=IntradaySide.LONG,
        quantity=Decimal("10"),
        bid=Decimal("100.00"),
        ask=Decimal("100.10"),
        stop_price=Decimal("99.50"),
        target_price=Decimal("101.50"),
        occurred_at=NOW,
        max_holding=timedelta(minutes=10),
        fee=Decimal("0.50"),
    )

    assert opened is not None
    assert opened.opportunity.entry_price == Decimal("100.10")
    assert opened.fill is not None
    assert opened.fill.action is IntradayTradeAction.BUY

    marked = await engine.mark_quote(
        source_event_id=_source(2),
        symbol="AAPL",
        strategy_id="support-reversal-v1",
        bid=Decimal("101.60"),
        ask=Decimal("101.70"),
        occurred_at=NOW + timedelta(minutes=2),
        exit_fee=Decimal("0.50"),
    )

    assert marked is not None
    assert marked.opportunity.status is IntradayOpportunityStatus.CLOSED
    assert marked.opportunity.close_reason is IntradayCloseReason.TARGET
    assert marked.opportunity.exit_price == Decimal("101.60")
    assert marked.opportunity.gross_pnl == Decimal("15.00")
    assert marked.opportunity.net_pnl == Decimal("14.00")
    assert marked.opportunity.mfe_percent == Decimal("1.4985")
    assert marked.fill is not None
    assert marked.fill.action is IntradayTradeAction.SELL
    assert len(store.events) == 2
    assert len(store.fills) == 2


@pytest.mark.unit
async def test_mark_tracks_unrealized_net_percent_and_excursions(
    engine: IntradayOpportunityEngine,
) -> None:
    await engine.open_position(
        source_event_id=_source(10),
        symbol="AAPL",
        strategy_id="vwap-reclaim-v1",
        side=IntradaySide.LONG,
        quantity=Decimal("20"),
        bid=Decimal("49.90"),
        ask=Decimal("50.00"),
        stop_price=Decimal("49.00"),
        target_price=Decimal("52.00"),
        occurred_at=NOW,
        max_holding=timedelta(minutes=15),
        fee=Decimal("1"),
    )

    event = await engine.mark_quote(
        source_event_id=_source(11),
        symbol="AAPL",
        strategy_id="vwap-reclaim-v1",
        bid=Decimal("50.50"),
        ask=Decimal("50.60"),
        occurred_at=NOW + timedelta(minutes=1),
    )

    assert event is not None
    assert event.opportunity.gross_pnl == Decimal("10.00")
    assert event.opportunity.net_pnl == Decimal("9.00")
    assert event.opportunity.net_pnl_percent == Decimal("0.9000")
    assert event.opportunity.mfe_percent == Decimal("1.0000")
    assert event.opportunity.mae_percent == Decimal("-0.2000")


@pytest.mark.unit
async def test_short_uses_bid_entry_ask_mark_and_time_exit(
    engine: IntradayOpportunityEngine,
) -> None:
    await engine.open_position(
        source_event_id=_source(20),
        symbol="TSLA",
        strategy_id="breakdown-v1",
        side=IntradaySide.SHORT,
        quantity=Decimal("5"),
        bid=Decimal("200.00"),
        ask=Decimal("200.10"),
        stop_price=Decimal("202.00"),
        target_price=Decimal("196.00"),
        occurred_at=NOW,
        max_holding=timedelta(minutes=5),
    )

    event = await engine.mark_quote(
        source_event_id=_source(21),
        symbol="TSLA",
        strategy_id="breakdown-v1",
        bid=Decimal("198.90"),
        ask=Decimal("199.00"),
        occurred_at=NOW + timedelta(minutes=5),
    )

    assert event is not None
    assert event.opportunity.close_reason is IntradayCloseReason.TIME_EXIT
    assert event.opportunity.entry_price == Decimal("200.00")
    assert event.opportunity.exit_price == Decimal("199.00")
    assert event.opportunity.gross_pnl == Decimal("5.00")
    assert event.opportunity.mfe_percent == Decimal("0.5000")


@pytest.mark.unit
async def test_end_of_day_closes_before_time_expiry(
    engine: IntradayOpportunityEngine,
) -> None:
    opened_at = datetime(2026, 8, 24, 19, 50, tzinfo=UTC)
    await engine.open_position(
        source_event_id=_source(30),
        symbol="NVDA",
        strategy_id="late-session-v1",
        side=IntradaySide.LONG,
        quantity=Decimal("1"),
        bid=Decimal("180"),
        ask=Decimal("180.10"),
        stop_price=Decimal("179"),
        target_price=Decimal("182"),
        occurred_at=opened_at,
        max_holding=timedelta(minutes=30),
    )

    event = await engine.mark_quote(
        source_event_id=_source(31),
        symbol="NVDA",
        strategy_id="late-session-v1",
        bid=Decimal("180.20"),
        ask=Decimal("180.30"),
        occurred_at=datetime(2026, 8, 24, 19, 55, tzinfo=UTC),
    )

    assert event is not None
    assert event.opportunity.close_reason is IntradayCloseReason.END_OF_DAY


@pytest.mark.unit
async def test_stop_closes_at_executable_quote(
    engine: IntradayOpportunityEngine,
) -> None:
    await engine.open_position(
        source_event_id=_source(35),
        symbol="MSFT",
        strategy_id="support-reversal-v1",
        side=IntradaySide.LONG,
        quantity=Decimal("2"),
        bid=Decimal("500"),
        ask=Decimal("500.10"),
        stop_price=Decimal("499"),
        target_price=Decimal("503"),
        occurred_at=NOW,
        max_holding=timedelta(minutes=10),
    )

    event = await engine.mark_quote(
        source_event_id=_source(36),
        symbol="MSFT",
        strategy_id="support-reversal-v1",
        bid=Decimal("498.90"),
        ask=Decimal("499"),
        occurred_at=NOW + timedelta(minutes=1),
    )

    assert event is not None
    assert event.opportunity.close_reason is IntradayCloseReason.STOP
    assert event.opportunity.exit_price == Decimal("498.90")


@pytest.mark.unit
async def test_duplicate_source_event_is_idempotent(
    engine: IntradayOpportunityEngine,
    store: InMemoryIntradayOpportunityStore,
) -> None:
    source_event_id = _source(40)
    kwargs = {
        "source_event_id": source_event_id,
        "symbol": "AMD",
        "strategy_id": "opening-range-v1",
        "side": IntradaySide.LONG,
        "quantity": Decimal("5"),
        "bid": Decimal("100"),
        "ask": Decimal("100.10"),
        "stop_price": Decimal("99"),
        "target_price": Decimal("102"),
        "occurred_at": NOW,
        "max_holding": timedelta(minutes=10),
    }

    first = await engine.open_position(**kwargs)
    duplicate = await engine.open_position(**kwargs)

    assert first is not None
    assert duplicate is None
    assert len(store.events) == 1


@pytest.mark.unit
async def test_one_active_per_symbol_and_strategy_but_multiple_round_trips(
    engine: IntradayOpportunityEngine,
    store: InMemoryIntradayOpportunityStore,
) -> None:
    base = {
        "symbol": "META",
        "strategy_id": "vwap-v1",
        "side": IntradaySide.LONG,
        "quantity": Decimal("1"),
        "bid": Decimal("600"),
        "ask": Decimal("600.10"),
        "stop_price": Decimal("599"),
        "target_price": Decimal("602"),
        "max_holding": timedelta(minutes=10),
    }
    await engine.open_position(source_event_id=_source(50), occurred_at=NOW, **base)

    concurrent_strategy = await engine.open_position(
        source_event_id=_source(54),
        occurred_at=NOW + timedelta(milliseconds=500),
        **{**base, "strategy_id": "opening-range-v1"},
    )

    assert concurrent_strategy is not None

    with pytest.raises(ActiveIntradayOpportunityError):
        await engine.open_position(
            source_event_id=_source(51),
            occurred_at=NOW + timedelta(seconds=1),
            **base,
        )

    await engine.close_position(
        source_event_id=_source(52),
        symbol="META",
        strategy_id="vwap-v1",
        bid=Decimal("600.50"),
        ask=Decimal("600.60"),
        occurred_at=NOW + timedelta(minutes=1),
        reason=IntradayCloseReason.MANUAL,
    )
    reopened = await engine.open_position(
        source_event_id=_source(53),
        occurred_at=NOW + timedelta(minutes=2),
        **base,
    )

    assert reopened is not None
    assert len(store.opportunities) == 3
    assert len(await store.list_session(NOW.date())) == 3
