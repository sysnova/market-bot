from datetime import timedelta
from decimal import Decimal

from app.contracts import BarTimeframe, MarketBar
from app.integration.short_observation_replay import hypothetical_short_outcome
from app.leveraged_thesis_engine.tests.test_v13 import NOW


async def test_warmup_analysis_is_not_counted_as_a_new_session_signal() -> None:
    from app.common.clock import FrozenClock
    from app.contracts import ANALYSIS_RESULT_EVENT, EventEnvelope
    from app.integration.short_observation_replay import _ReplayPublisher
    from app.leveraged_thesis_engine.tests.test_v13 import _intraday

    recorder = _ReplayPublisher(FrozenClock(NOW), "ASTS")
    old = _intraday().model_copy(update={"as_of": NOW - timedelta(days=1)})
    await recorder.publish(
        "test",
        EventEnvelope(
            event_type=ANALYSIS_RESULT_EVENT, source="test", occurred_at=NOW, payload=old
        ),
    )
    assert recorder.mature == []


def _bar(at: object, *, high: str = "61", low: str = "58") -> MarketBar:
    return MarketBar(
        symbol="ASTS",
        timestamp=at,
        timeframe=BarTimeframe.MINUTE_1,
        open=Decimal("60"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal("60"),
        volume=Decimal("100"),
        source="test",
        feed="sip",
        is_final=True,
    )


def test_outcome_excludes_signal_bar_and_marks_ambiguous_ohlc() -> None:
    bars = (_bar(NOW - timedelta(minutes=1)), _bar(NOW, high="63", low="57"))
    result = hypothetical_short_outcome(
        bars, available_at=NOW, stop=Decimal("62"), target=Decimal("58")
    )
    assert result["entry_time"] == NOW.isoformat()
    assert result["first_level"] == "AMBIGUOUS_BOTH"
    assert result["return_percent"] is None


def test_bad_entry_geometry_is_not_counted_as_a_trade() -> None:
    result = hypothetical_short_outcome(
        (_bar(NOW),), available_at=NOW, stop=Decimal("59"), target=Decimal("58")
    )
    assert result["first_level"] == "NOT_ENTERABLE"
