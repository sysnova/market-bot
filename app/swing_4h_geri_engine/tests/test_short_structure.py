from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts import BarTimeframe
from app.swing_4h_geri_engine.models import Swing4HGeriContext
from app.swing_4h_geri_engine.short_structure import ShortRules, short_metrics
from app.swing_4h_geri_engine.tests.test_recovery import context


def short_context() -> Swing4HGeriContext:
    c = context()
    template = c.bars[0]
    daily = []
    for i in range(55):
        low, high, close = ("108", "115", "112")
        if i == 5:
            low, high, close = ("80", "115", "110")
        if i == 45:
            low, high, close = ("95", "105", "100")
        if i >= 50:
            low, high, close = ("89", "105", "92")
        daily.append(
            template.model_copy(
                update={
                    "timeframe": BarTimeframe.DAY_1,
                    "timestamp": datetime(2026, 5, 1, 4, tzinfo=UTC) + timedelta(days=i),
                    "open": Decimal(close) + 1,
                    "high": Decimal(high),
                    "low": Decimal(low),
                    "close": Decimal(close),
                }
            )
        )
    fast = tuple(
        template.model_copy(
            update={
                "timeframe": BarTimeframe.MINUTE_15,
                "timestamp": datetime(2026, 7, 13, 15, tzinfo=UTC) + timedelta(days=i),
                "open": Decimal("94"),
                "high": Decimal("95"),
                "low": Decimal("91"),
                "close": Decimal("93"),
                "volume": Decimal("100"),
            }
        )
        for i in range(5)
    )
    fast += (
        fast[-1].model_copy(
            update={
                "timestamp": datetime(2026, 7, 22, 14, 45, tzinfo=UTC),
            }
        ),
        fast[-1].model_copy(
            update={
                "timestamp": datetime(2026, 7, 22, 15, tzinfo=UTC),
                "open": Decimal("93"),
                "high": Decimal("94"),
                "low": Decimal("89"),
                "close": Decimal("90"),
                "volume": Decimal("200"),
            }
        ),
    )
    return replace(
        c,
        daily_bars=tuple(daily),
        confirmation_bars=fast,
        current_price=Decimal("90"),
        as_of=fast[-1].timestamp + timedelta(minutes=15),
        current_price_at=fast[-1].timestamp + timedelta(minutes=15),
    )


def test_short_never_uses_countertrend_namespace_or_long_objective() -> None:
    c = short_context()
    m = {x.name: x.value for x in short_metrics(c, ShortRules())}
    assert not any(k.startswith("countertrend_") for k in m)
    assert m["short_target"] < c.current_price < m["short_invalidation"]
    assert m["short_pivot_vwap"] > c.current_price
    assert m["short_rvol"] == Decimal("2")
    # A valid direction alone does not bypass insufficient reward/risk.
    assert m["short_eligible"] is False
    assert "insufficient_reward_risk" in m["short_reasons"]


def test_missing_daily_bars_are_unavailable_not_bearish() -> None:
    m = {x.name: x.value for x in short_metrics(context(), ShortRules())}
    assert m["short_state"] == "UNAVAILABLE"
    assert m["short_eligible"] is False
    assert "short_target" not in m


def test_high_bullish_volume_does_not_confirm_short() -> None:
    c = short_context()
    fast = (
        *c.confirmation_bars[:-1],
        c.confirmation_bars[-1].model_copy(
            update={
                "open": Decimal("89"),
                "close": Decimal("92"),
            }
        ),
    )
    m = {x.name: x.value for x in short_metrics(replace(c, confirmation_bars=fast), ShortRules())}
    assert "bearish_price_confirmation_missing" in m["short_reasons"]
    assert m["short_eligible"] is False


def test_short_requires_all_its_own_gates_and_can_confirm_with_lower_target() -> None:
    c = short_context()
    c = replace(
        c,
        daily_bars=tuple(
            x.model_copy(update={"low": Decimal("79")}) if i >= 50 else x
            for i, x in enumerate(c.daily_bars)
        ),
    )
    a = c.bars[-1].model_copy(
        update={
            "timestamp": datetime(2026, 7, 22, 13, 30, tzinfo=UTC),
            "low": Decimal("88"),
            "high": Decimal("94"),
            "open": Decimal("90"),
            "close": Decimal("92"),
        }
    )
    b = a.model_copy(
        update={
            "timestamp": datetime(2026, 7, 22, 17, 30, tzinfo=UTC),
            "low": Decimal("87"),
            "high": Decimal("92"),
            "close": Decimal("89"),
        }
    )
    fast = (
        *c.confirmation_bars[:-2],
        *tuple(
            x.model_copy(
                update={
                    "timestamp": x.timestamp + timedelta(days=1),
                }
            )
            for x in c.confirmation_bars[-2:]
        ),
    )
    c = replace(
        c,
        bars=(*c.bars, a, b),
        confirmation_bars=fast,
        current_price_at=fast[-1].timestamp + timedelta(minutes=15),
        as_of=fast[-1].timestamp + timedelta(minutes=15),
    )
    m = {x.name: x.value for x in short_metrics(c, ShortRules())}
    assert m["short_eligible"] is True, m["short_reasons"]
    assert m["short_target"] == Decimal("80")
    assert m["short_invalidation"] == Decimal("94")
    assert m["short_reward_risk"] == Decimal("2.5")
