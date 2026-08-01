from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from app.contracts import (
    BarTimeframe,
    MacroRegime,
    MarketBar,
    PatreonCapsState,
    PatreonCapsTransition,
    new_uuid7,
)
from app.patreon_caps_engine import replay_outcomes

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _transition(
    watch_id: UUID,
    state: PatreonCapsState,
    occurred_at: datetime,
) -> PatreonCapsTransition:
    return PatreonCapsTransition(
        watch_id=watch_id,
        symbol="NVO",
        previous_state=(
            None
            if state is PatreonCapsState.WATCH_ZONE
            else PatreonCapsState.SUPPORT_TEST
        ),
        state=state,
        occurred_at=occurred_at,
        rule_version="1.0.0",
        current_price=Decimal("100"),
        zone_low=Decimal("96"),
        zone_center=Decimal("98"),
        zone_high=Decimal("100"),
        invalidation=Decimal("95"),
        confluence_score=Decimal("80"),
        confirmation_score=Decimal("80"),
        alignment_score=Decimal("100"),
        patreon_score=Decimal("86"),
        macro_regime=MacroRegime.RISK_ON,
        reasons=("fixture",),
        expires_at=occurred_at + timedelta(days=56),
    )


def _bar(timestamp: datetime, close: Decimal) -> MarketBar:
    return MarketBar(
        symbol="NVO",
        timeframe=BarTimeframe.DAY_1,
        timestamp=timestamp,
        open=close,
        high=close + Decimal("1"),
        low=close - Decimal("2"),
        close=close,
        volume=Decimal("1000"),
        source="fixture",
        feed="test",
    )


def test_replay_uses_only_future_final_bars_and_measures_all_horizons() -> None:
    watch_id = new_uuid7()
    watch = _transition(watch_id, PatreonCapsState.WATCH_ZONE, NOW)
    buy_at = NOW + timedelta(days=2)
    buy = _transition(watch_id, PatreonCapsState.CONFIRMED_V, buy_at)
    future = []
    for day in range(1, 61):
        close = Decimal("89") if day == 10 else Decimal(100 + day)
        future.append(_bar(buy_at + timedelta(days=day), close))
    bars = (
        _bar(buy_at - timedelta(days=1), Decimal("1000")),
        *future,
    )

    outcome = replay_outcomes((watch, buy), {"NVO": bars})[0]

    assert outcome.return_5d == Decimal("5.0000")
    assert outcome.return_20d == Decimal("20.0000")
    assert outcome.return_60d == Decimal("60.0000")
    assert outcome.mfe_percent == Decimal("61.0000")
    assert outcome.mae_percent == Decimal("-13.0000")
    assert outcome.invalidated is True
    assert outcome.confirmation_hours == Decimal("48.0000")


def test_replay_reports_incomplete_horizons_without_look_ahead() -> None:
    buy = _transition(new_uuid7(), PatreonCapsState.CONFIRMED_BASE, NOW)

    outcome = replay_outcomes((buy,), {"NVO": (_bar(NOW + timedelta(days=1), Decimal("101")),)})[0]

    assert outcome.return_5d is None
    assert outcome.confirmation_hours is None
